import os
import re
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
CREDENTIALS_FILE = 'credentials.json'
SPREADSHEET_NAME = '香港網絡與傳輸工程師職位追蹤表 (Job Tracking Master)'
SHEET_TAB_NAME = 'Daily Job Logs'

# 改用 ListedDate 排序，確保能抓到最新發佈職位
TARGET_CONFIGS = [
    {
        "category": "Network",
        "url": "https://hk.jobsdb.com/network-engineer-jobs?sortmode=ListedDate"
    },
    {
        "category": "Transmission",
        "url": "https://hk.jobsdb.com/transmission-engineer-jobs?sortmode=ListedDate"
    }
]

def init_google_sheet():
    creds_json_str = os.environ.get('GCP_CREDENTIALS_JSON')
    if creds_json_str:
        info = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    
    gc = gspread.authorize(creds)
    sh = gc.open(SPREADSHEET_NAME)
    worksheet = sh.worksheet(SHEET_TAB_NAME)
    return worksheet

def parse_salary(salary_text):
    if not salary_text or salary_text == "Negotiable":
        return "", ""
    nums = re.findall(r'\d[\d,]*', salary_text)
    clean_nums = [n.replace(',', '') for n in nums if int(n.replace(',', '')) > 5000]
    if len(clean_nums) >= 2:
        return clean_nums[0], clean_nums[1]
    elif len(clean_nums) == 1:
        return clean_nums[0], ""
    return "", ""

def scrape_jobs():
    scraped_data = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu'
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for config in TARGET_CONFIGS:
            cat = config["category"]
            target_url = config["url"]
            print(f"[*] 正在掃描 {cat} 類別: {target_url}")

            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                # 等待職位標題元素出現，最多等 15 秒
                page.wait_for_selector("a[data-automation='jobTitle']", timeout=15000)
            except Exception as e:
                print(f"[!] 等待職位載入超時或警告: {e}")

            # 滾動頁面觸發延遲載入 (Lazy Loading)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
            page.wait_for_timeout(2000)

            # 抓取所有包含職位標題的 a 標籤
            title_elements = page.query_selector_all("a[data-automation='jobTitle']")
            print(f" -> 找到 {len(title_elements)} 個職位標題")

            for title_elem in title_elements:
                try:
                    job_title = title_elem.inner_text().strip()
                    href = title_elem.get_attribute("href")
                    if not href:
                        continue
                    direct_url = href if href.startswith("http") else f"https://hk.jobsdb.com{href.split('?')[0]}"

                    # 透過父層容器定位該職位的卡片區塊
                    card = title_elem.evaluate_handle("el => el.closest('article') || el.closest('[data-automation=\"normalJob\"]') || el.closest('div')")

                    # 抓取公司名稱
                    company_elem = card.query_selector("a[data-automation='jobCompany']") if card else None
                    company = company_elem.inner_text().strip() if company_elem else "N/A"

                    # 抓取地點
                    location_elem = card.query_selector("span[data-automation='jobLocation']") if card else None
                    location = location_elem.inner_text().strip() if location_elem else ""
                    company_field = f"{company} ({location})" if location else company

                    # 抓取薪資
                    salary_elem = card.query_selector("span[data-automation='jobSalary']") if card else None
                    salary_text = salary_elem.inner_text().strip() if salary_elem else "Negotiable"
                    salary_min, salary_max = parse_salary(salary_text)

                    row = [
                        today_str,
                        cat,
                        job_title,
                        company_field,
                        salary_min,
                        salary_max,
                        "參考詳情",
                        "詳見連結",
                        "-",
                        "JobsDB",
                        direct_url,
                        "Open"
                    ]
                    scraped_data.append(row)
                    print(f"    [OK] 成功解析: {job_title} | {company}")
                except Exception as e:
                    print(f"    [!] 解析單一職位失敗: {e}")
                    continue

        browser.close()
    return scraped_data

def sync_to_sheet(sheet, new_rows):
    if not new_rows:
        print("[i] 抓取結果為 0 筆，未寫入試算表。")
        return

    # 讀取試算表中第 11 欄 (Job Link) 進行去重比對
    try:
        existing_links = set(sheet.col_values(11)[1:])
    except Exception:
        existing_links = set()

    to_insert = []
    for row in new_rows:
        link = row[10]
        if link not in existing_links:
            to_insert.append(row)
            existing_links.add(link)

    print(f"[*] 準備寫入 {len(to_insert)} 筆全新資料...")
    if to_insert:
        sheet.append_rows(to_insert, value_input_option="USER_ENTERED")
        print(f"[+] 成功寫入 {len(to_insert)} 個全新真實職位！")
    else:
        print("[i] 抓取到的職位均已存在於試算表中（無新職位）。")

if __name__ == "__main__":
    print("[*] 開始抓取職位資料...")
    worksheet = init_google_sheet()
    jobs = scrape_jobs()
    print(f"[*] 爬蟲抓取完成，共解析出 {len(jobs)} 個職位。")
    sync_to_sheet(worksheet, jobs)
    print("[*] 全部完成！")
