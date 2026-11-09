import os
import re
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

# 1. Google Sheets 設定
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
CREDENTIALS_FILE = 'credentials.json'  # 剛才下載的 Service Account JSON
SPREADSHEET_NAME = '香港網絡與傳輸工程師職位追蹤表 (Job Tracking Master)'
SHEET_TAB_NAME = 'Daily Job Logs'

# 2. 爬取目標 (JobsDB 過去 24 小時篩選網址)
TARGET_CONFIGS = [
    {
        "category": "Network",
        # daterange=1 代表過去 24 小時
        "url": "https://hk.jobsdb.com/network-engineer-jobs?daterange=1"
    },
    {
        "category": "Transmission",
        "url": "https://hk.jobsdb.com/transmission-engineer-jobs?daterange=1"
    }
]

def init_google_sheet():
    # 優先從環境變數讀取 GCP 憑證字串
    creds_json_str = os.environ.get('GCP_CREDENTIALS_JSON')
    if creds_json_str:
        info = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        # 本地測試時從檔案讀取
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)

    gc = gspread.authorize(creds)
    sh = gc.open(SPREADSHEET_NAME)
    worksheet = sh.worksheet(SHEET_TAB_NAME)
    return worksheet

def scrape_jobs():
    scraped_data = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    with sync_playwright() as p:
        # 啟動無頭瀏覽器，設置 User-Agent 避免反爬
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for config in TARGET_CONFIGS:
            cat = config["category"]
            target_url = config["url"]
            print(f"[*] 正在掃描 {cat} 類別: {target_url}")

            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)  # 等待動態 DOM 載入

            # 抓取所有職位卡片（JobsDB 採用 article 標籤包覆單一職位）
            cards = page.query_selector_all("article")
            print(f" -> 找到 {len(cards)} 個候選職位卡片")

            for card in cards:
                try:
                    # 抓取職稱與真實 Direct Link
                    title_elem = card.query_selector("a[data-automation='jobTitle']")
                    if not title_elem:
                        continue
                    
                    job_title = title_elem.inner_text().strip()
                    href = title_elem.get_attribute("href")
                    # 拼接完整 Direct URL
                    direct_url = href if href.startswith("http") else f"https://hk.jobsdb.com{href.split('?')[0]}"

                    # 抓取公司名稱
                    company_elem = card.query_selector("a[data-automation='jobCompany']")
                    company = company_elem.inner_text().strip() if company_elem else "N/A"

                    # 抓取地點 / 行業
                    location_elem = card.query_selector("span[data-automation='jobLocation']")
                    location = location_elem.inner_text().strip() if location_elem else ""
                    company_field = f"{company} ({location})" if location else company

                    # 抓取薪酬（若雇主有提供）
                    salary_elem = card.query_selector("span[data-automation='jobSalary']")
                    salary_text = salary_elem.inner_text().strip() if salary_elem else "Negotiable"

                    # 解析薪酬數字（簡易解析）
                    salary_min, salary_max = parse_salary(salary_text)

                    # 封裝資料行 (對應 Google Sheet 欄位順序)
                    row = [
                        today_str,          # Date Added
                        cat,                # Category
                        job_title,          # Job Title
                        company_field,      # Company / Industry
                        salary_min,         # Salary Min (HKD)
                        salary_max,         # Salary Max (HKD)
                        "參考詳情",          # Exp Required
                        "詳見連結",          # Key Skills
                        "-",                # Certifications
                        "JobsDB",           # Platform
                        direct_url,         # Job Link (真實直接連結)
                        "Open"              # Notes / Status
                    ]
                    scraped_data.append(row)
                except Exception as e:
                    print(f"[!] 解析職位出錯: {e}")
                    continue

        browser.close()
    return scraped_data

def parse_salary(salary_text):
    """提取薪資區間字串中的數值"""
    if not salary_text or salary_text == "Negotiable":
        return "", ""
    nums = re.findall(r'\d[\d,]*', salary_text)
    clean_nums = [n.replace(',', '') for n in nums if int(n.replace(',', '')) > 5000]
    if len(clean_nums) >= 2:
        return clean_nums[0], clean_nums[1]
    elif len(clean_nums) == 1:
        return clean_nums[0], ""
    return "", ""

def sync_to_sheet(sheet, new_rows):
    if not new_rows:
        print("[i] 過去 24 小時未發現全新職位。")
        return

    # 讀取現有 Job Link 避免重複寫入
    existing_links = set(sheet.col_values(11)[1:])  # 第 11 欄為 Job Link
    to_insert = []

    for row in new_rows:
        link = row[10]
        if link not in existing_links:
            to_insert.append(row)
            existing_links.add(link)

    if to_insert:
        sheet.append_rows(to_insert, value_input_option="USER_ENTERED")
        print(f"[+] 成功寫入 {len(to_insert)} 個全新真實職位！")
    else:
        print("[i] 抓取到的職位均已存在於表內，無需重複更新。")

if __name__ == "__main__":
    print("[*] 開始抓取 24 小時內全新職位...")
    worksheet = init_google_sheet()
    jobs = scrape_jobs()
    sync_to_sheet(worksheet, jobs)
    print("[*] 完成！")
