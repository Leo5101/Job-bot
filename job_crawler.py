import os
import json
from datetime import datetime
import pandas as pd
from jobspy import scrape_jobs
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
CREDENTIALS_FILE = 'credentials.json'
SPREADSHEET_NAME = '香港網絡與傳輸工程師職位追蹤表 (Job Tracking Master)'
SHEET_TAB_NAME = 'Daily Job Logs'

SEARCH_CONFIGS = [
    {"category": "Network", "term": "Network Engineer"},
    {"category": "Transmission", "term": "Transmission Engineer OR Submarine Cable OR DWDM"}
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
    return sh.worksheet(SHEET_TAB_NAME)

def fetch_all_jobs():
    scraped_rows = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for cfg in SEARCH_CONFIGS:
        cat = cfg["category"]
        term = cfg["term"]
        print(f"[*] 正在搜尋 {cat} 職位: {term}")

        try:
            # 同時從 Indeed 及 LinkedIn 抓取香港過去 72 小時職位
            jobs_df = scrape_jobs(
                site_name=["indeed", "linkedin"],
                search_term=term,
                location="Hong Kong",
                results_wanted=25,
                hours_old=72,
                country_indeed='Hong Kong'
            )
            print(f" -> 成功獲取 {len(jobs_df)} 筆職位")

            for _, job in jobs_df.iterrows():
                title = str(job.get("title", "")).strip()
                company = str(job.get("company", "N/A")).strip()
                loc = str(job.get("location", "HK")).strip()
                company_field = f"{company} ({loc})" if loc and loc != "nan" else company

                min_sal = str(job.get("min_amount", "")) if pd.notna(job.get("min_amount")) else ""
                max_sal = str(job.get("max_amount", "")) if pd.notna(job.get("max_amount")) else ""
                job_url = str(job.get("job_url", "")).strip()
                site = str(job.get("site", "JobBoard")).capitalize()

                if not job_url or job_url == "nan":
                    continue

                scraped_rows.append([
                    today_str,
                    cat,
                    title,
                    company_field,
                    min_sal,
                    max_sal,
                    "參考詳情",
                    "詳見連結",
                    "-",
                    site,
                    job_url,
                    "Open"
                ])
                print(f"    [OK] {site}: {title} | {company}")
        except Exception as e:
            print(f"[!] 搜尋 {cat} 時發生錯誤: {e}")

    return scraped_rows

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
    print("[*] 啟動多平台職位監測任務...")
    worksheet = init_google_sheet()
    jobs = fetch_all_jobs()
    print(f"[*] 任務抓取完畢，共解析出 {len(jobs)} 個職位。")
    sync_to_sheet(worksheet, jobs)
    print("[*] 全部完成！")
