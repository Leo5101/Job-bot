import os
import re
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

# 針對 Network 及 Transmission 領域的核心技能字典 (支援自動大小寫正規化輸出)
SKILL_PATTERNS = {
    # 路由與交換 (Routing & Switching)
    r'\bBGP\b': 'BGP',
    r'\bOSPF\b': 'OSPF',
    r'\bMPLS\b': 'MPLS',
    r'\bVLAN\w*\b': 'VLAN',
    r'\bSTP\b|\bSpanning Tree\b': 'STP',
    r'\bL2/L3\b|\bLayer 2\b|\bLayer 3\b': 'L2/L3 Routing & Switching',
    r'\bVXLAN\b': 'VXLAN',
    r'\bEVPN\b': 'EVPN',
    # 網路設備與防火牆廠商 (Vendors & Security)
    r'\bCisco\b|\bCatalyst\b|\bNexus\b': 'Cisco (Catalyst/Nexus)',
    r'\bFortiGate\b|\bFortinet\b': 'Fortinet/FortiGate',
    r'\bPalo Alto\b': 'Palo Alto Firewall',
    r'\bCheck\s*Point\b': 'Check Point',
    r'\bHuawei\b': 'Huawei Network',
    r'\bH3C\b': 'H3C',
    r'\bJuniper\b|\bJunos\b': 'Juniper',
    r'\bAruba\b': 'Aruba',
    r'\bF5\b': 'F5 Load Balancer',
    # 廣域網與架構 (WAN, Cloud & Architecture)
    r'\bSD-WAN\b|\bSDWAN\b': 'SD-WAN',
    r'\bIPsec\b|\bVPN\b': 'IPsec/VPN',
    r'\bWi-Fi\b|\bWireless\b|\bWLAN\b': 'Enterprise Wi-Fi/WLAN',
    r'\bData\s*Cent(?:er|re)\b': 'Data Centre Networking',
    r'\bCutover\b|\bMigration\b': 'Network Migration/Cutover',
    r'\bFirewall\b': 'Firewall Management',
    r'\bSolarWinds\b|\bPRTG\b|\bZabbix\b|\bNagios\b': 'Network Monitoring (NMS)',
    r'\bPython\b|\bAnsible\b|\bAutomation\b': 'Network Automation (Python/Ansible)',
    # 海底光纜與傳輸技術 (Transmission & Submarine Cable)
    r'\bDWDM\b': 'DWDM',
    r'\bOTN\b': 'OTN',
    r'\bSLTE\b': 'SLTE',
    r'\bSubmarine\s*Cable\b|\bSubsea\b': 'Submarine Cable Systems',
    r'\bSDH\b|\bSONET\b': 'SDH/SONET',
    r'\bOptical\b|\bFiber\b|\bFibre\b': 'Optical Fiber Transmission',
    r'\bCiena\b|\bInfinera\b|\bNokia\b|\bAlcatel\b': 'Optical Vendor (Ciena/Nokia/Infinera)',
    r'\bROADM\b': 'ROADM',
    r'\bOTDR\b|\bOSA\b|\bBER\b': 'Optical Testing (OTDR/BER)'
}

# 專業證書提取規則
CERT_PATTERNS = {
    r'\bCCNA\b': 'CCNA',
    r'\bCCNP\b': 'CCNP',
    r'\bCCIE\b': 'CCIE',
    r'\bHCIA\b': 'HCIA',
    r'\bHCIP\b': 'HCIP',
    r'\bHCIE\b': 'HCIE',
    r'\bNSE\s*[4-8]\b|\bFCNSA\b|\bFCNSP\b': 'Fortinet NSE',
    r'\bPCNSA\b|\bPCNSE\b': 'Palo Alto PCNSA/PCNSE',
    r'\bJNCIA\b|\bJNCIS\b|\bJNCIP\b|\bJNCIE\b': 'Juniper Cert',
    r'\bCISSP\b|\bCISA\b': 'CISSP/CISA',
    r'\bITIL\b': 'ITIL',
    r'\bPMP\b': 'PMP'
}

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

def extract_experience(text):
    """從 JD 內文提取年資要求，格式化為 'X - Y 年' 或 'X+ 年'"""
    if not text or not isinstance(text, str):
        return "未列明"
    
    # 1. 匹配範圍格式：例如 "3 - 5 years", "3 to 5 yrs", "4-6 years of experience"
    range_match = re.search(
        r'(\d{1,2})\s*(?:-|to|–|~)\s*(\d{1,2})\+?\s*(?:years?|yrs?)',
        text, re.IGNORECASE
    )
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        if 0 < low < high <= 20:
            return f"{low} - {high} 年"

    # 2. 匹配單一年資要求：例如 "minimum 3 years", "at least 5 years", "3+ years"
    single_patterns = [
        r'(?:minimum|min\.?|at least|over|above|more than)\s+(?:of\s+)?(\d{1,2})\+?\s*(?:years?|yrs?)',
        r'(\d{1,2})\+\s*(?:years?|yrs?)',
        r'(\d{1,2})\s*(?:years?|yrs?)\s+(?:or above|or more|of\s+\w+\s+experience|of\s+experience|relevant\s+experience|solid\s+experience|hands-on\s+experience)'
    ]
    for pat in single_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            yrs = int(m.group(1))
            if 0 < yrs <= 20:
                return f"{yrs}+ 年"

    return "未列明"

def extract_skills(text, title=""):
    """從標題與 JD 內文提取關鍵技術詞彙"""
    combined = f"{title} {text}" if isinstance(text, str) else str(title)
    found_skills = []
    for pattern, label in SKILL_PATTERNS.items():
        if re.search(pattern, combined, re.IGNORECASE):
            if label not in found_skills:
                found_skills.append(label)
    
    if found_skills:
        return ", ".join(found_skills[:6])  # 取最核心的前 6 個技能，保持表格整潔
    return "General Network Support"

def extract_certs(text):
    """從 JD 內文提取證書要求"""
    if not text or not isinstance(text, str):
        return "-"
    found_certs = []
    for pattern, label in CERT_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            if label not in found_certs:
                found_certs.append(label)
    return ", ".join(found_certs) if found_certs else "-"

def fetch_all_jobs():
    scraped_rows = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for cfg in SEARCH_CONFIGS:
        cat = cfg["category"]
        term = cfg["term"]
        print(f"[*] 正在搜尋 {cat} 職位: {term}")

        try:
            # 開啟 linkedin_fetch_description=True 以獲取 LinkedIn 職位完整內文
            jobs_df = scrape_jobs(
                site_name=["indeed", "linkedin"],
                search_term=term,
                location="Hong Kong",
                results_wanted=20,
                hours_old=72,
                country_indeed='Hong Kong',
                linkedin_fetch_description=True
            )
            print(f" -> 成功獲取 {len(jobs_df)} 筆職位")

            for _, job in jobs_df.iterrows():
                title = str(job.get("title", "")).strip()
                company = str(job.get("company", "N/A")).strip()
                loc = str(job.get("location", "HK")).strip()
                company_field = f"{company} ({loc})" if loc and loc != "nan" else company

                min_sal = str(int(job["min_amount"])) if pd.notna(job.get("min_amount")) else ""
                max_sal = str(int(job["max_amount"])) if pd.notna(job.get("max_amount")) else ""
                job_url = str(job.get("job_url", "")).strip()
                site = str(job.get("site", "JobBoard")).capitalize()
                description = str(job.get("description", "")) if pd.notna(job.get("description")) else ""

                if not job_url or job_url == "nan":
                    continue

                # 解析年資、技能與證書
                exp_req = extract_experience(description)
                key_skills = extract_skills(description, title)
                certs_req = extract_certs(description)

                scraped_rows.append([
                    today_str,
                    cat,
                    title,
                    company_field,
                    min_sal,
                    max_sal,
                    exp_req,
                    key_skills,
                    certs_req,
                    site,
                    job_url,
                    "Open"
                ])
                print(f"    [OK] {site}: {title} | 年資: {exp_req} | 技能: {key_skills}")
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
    print("[*] 啟動多平台職位深度解析任務...")
    worksheet = init_google_sheet()
    jobs = fetch_all_jobs()
    print(f"[*] 任務抓取完畢，共解析出 {len(jobs)} 個職位。")
    sync_to_sheet(worksheet, jobs)
    print("[*] 全部完成！")
