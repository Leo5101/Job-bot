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
    nums = re.findall(r'\d[\d,]*', str(salary_text))
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
        # 使用新版 headless 模式並隱藏 Automation 特徵旗標
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--window-size=1920,1080'
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            locale="en-HK"
        )
        
        # 注入反偵測 JS，抹除 navigator.webdriver 特徵
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['en-HK', 'en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """)

        page = context.new_page()

        for config in TARGET_CONFIGS:
            cat = config["category"]
            target_url = config["url"]
            print(f"[*] 正在掃描 {cat} 類別: {target_url}")

            api_captured_jobs = []

            # 監聽網頁背景發出的真實搜尋 API 回應
            def handle_response(response):
                if ("search" in response.url or "graphql" in response.url) and response.status == 200:
                    try:
                        ctype = response.headers.get("content-type", "")
                        if "application/json" in ctype:
                            data = response.json()
                            if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                                api_captured_jobs.extend(data["data"])
                    except Exception:
                        pass

            page.on("response", handle_response)

            try:
                resp = page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
                status_code = resp.status if resp else "Unknown"
                page_title = page.title()
                print(f" -> HTTP 狀態碼: {status_code} | 頁面標題: {page_title}")
            except Exception as e:
                print(f"[!] 頁面導航警告: {e}")

            page.remove_listener("response", handle_response)

            # 途徑 1：如果成功從背景攔截到 JSON 資料
            if api_captured_jobs:
                print(f" -> [途徑 1: 背景 API 攔截] 成功捕獲 {len(api_captured_jobs)} 筆職位資料")
                for item in api_captured_jobs:
                    if not isinstance(item, dict) or not item.get("title"):
                        continue
                    job_id = item.get("id", "")
                    job_title = item.get("title", "").strip()
                    company = item.get("advertiser", {}).get("description", "N/A") if isinstance(item.get("advertiser"), dict) else "N/A"
                    location = item.get("location", "")
                    company_field = f"{company} ({location})" if location else company
                    salary_text = item.get("salary", "Negotiable")
                    salary_min, salary_max = parse_salary(salary_text)
                    direct_url = f"https://hk.jobsdb.com/job/{job_id}"

                    scraped_data.append([
                        today_str, cat, job_title, company_field,
                        salary_min, salary_max, "參考詳情", "詳見連結",
                        "-", "JobsDB", direct_url, "Open"
                    ])
                    print(f"    [OK] 提取職位: {job_title} | {company}")
                continue

            # 途徑 2：從 DOM 結構直接提取
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
            page.wait_for_timeout(2000)

            title_elements = page.query_selector_all("a[data-automation='jobTitle'], article a[href*='/job/']")
            print(f" -> [途徑 2: DOM 解析] 找到 {len(title_elements)} 個職位連結元素")

            seen_urls = set()
            for title_elem in title_elements:
                try:
                    job_title = title_elem.inner_text().strip()
                    href = title_elem.get_attribute("href")
                    if not href or not job_title or "/job/" not in href:
                        continue
                    
                    direct_url = href if href.startswith("http") else f"https://hk.jobsdb.com{href.split('?')[0]}"
                    if direct_url in seen_urls:
                        continue
                    seen_urls.add(direct_url)

                    card = title_elem.evaluate_handle("el => el.closest('article') || el.closest('div')")
                    company_elem = card.query_selector("[data-automation='jobCompany']") if card else None
                    company = company_elem.inner_text().strip() if company_elem else "N/A"

                    location_elem = card.query_selector("[data-automation='jobLocation']") if card else None
                    location = location_elem.inner_text().strip() if location_elem else ""
                    company_field = f"{company} ({location})" if location else company

                    salary_elem = card.query_selector("[data-automation='jobSalary']") if card else None
                    salary_text = salary_elem.inner_text().strip() if salary_elem else "Negotiable"
                    salary_min, salary_max = parse_salary(salary_text)

                    scraped_data.append([
                        today_str, cat, job_title, company_field,
                        salary_min, salary_max, "參考詳情", "詳見連結",
                        "-", "JobsDB", direct_url, "Open"
                    ])
                    print(f"    [OK] 成功解析: {job_title} | {company}")
                except Exception:
                    continue

        browser.close()
    return scraped_data

def sync_to_sheet(sheet, new_rows):
    if not new_rows:
        print("[i] 抓取結果為 0 筆，未寫入試算表。")
        return

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
        print("[i] 所有抓取職位均已存在於試算表中（無新職位）。")

if __name__ == "__main__":
    print("[*] 啟動 Stealth 職位監測任務...")
    worksheet = init_google_sheet()
    jobs = scrape_jobs()
    print(f"[*] 任務抓取完畢，共解析出 {len(jobs)} 個職位。")
    sync_to_sheet(worksheet, jobs)
    print("[*] 全部完成！")
