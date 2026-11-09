import os
import re
import json
import urllib.request
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
CREDENTIALS_FILE = 'credentials.json'
SPREADSHEET_NAME = '香港網絡與傳輸工程師職位追蹤表 (Job Tracking Master)'
SHEET_TAB_NAME = 'Daily Job Logs'

CATEGORIES = [
    {"category": "Network", "keywords": "Network Engineer"},
    {"category": "Transmission", "keywords": "Transmission Engineer"}
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

def fetch_jobs_via_api():
    scraped_data = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://hk.jobsdb.com/"
    }

    for cat_info in CATEGORIES:
        cat = cat_info["category"]
        kw = cat_info["keywords"]
        encoded_kw = urllib.parse.quote(kw)
        
        # JobsDB (SEEK) 職位搜尋 API
        api_url = f"https://hk.jobsdb.com/api/chalice-search/v4/search?siteKey=HK-Main&sourcesystem=houston&keywords={encoded_kw}&sortmode=ListedDate&pageSize=30"
        print(f"[*] 正在請求 {cat} 職位 API: {kw}")

        req = urllib.request.Request(api_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                job_list = data.get("data", [])
                print(f" -> API 回傳成功，取得 {len(job_list)} 筆職位")

                for item in job_list:
                    job_id = item.get("id")
                    job_title = item.get("title", "").strip()
                    company = item.get("advertiser", {}).get("description", "N/A").strip()
                    location = item.get("location", "").strip()
                    company_field = f"{company} ({location})" if location else company

                    salary_text = item.get("salary", "Negotiable")
                    salary_min, salary_max = parse_salary(salary_text)
                    direct_url = f"https://hk.jobsdb.com/job/{job_id}"

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
                    print(f"    [OK] 提取職位: {job_title} | {company}")
        except Exception as e:
            print(f"[!] API 請求失敗 ({cat}): {e}")

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
    print("[*] 啟動 API 職位監測任務...")
    worksheet = init_google_sheet()
    jobs = fetch_jobs_via_api()
    print(f"[*] 任務抓取完畢，共解析出 {len(jobs)} 個職位。")
    sync_to_sheet(worksheet, jobs)
    print("[*] 全部完成！")
