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

# 使用雙引號精準鎖定職位詞組，減少不相關職位
SEARCH_CONFIGS = [
    {
        "category": "Network",
        "term": '"Network Engineer" OR "Network Specialist" OR "Network Administrator" OR "Network Infrastructure"'
    },
    {
        "category": "Transmission",
        "term": '"Transmission Engineer" OR "Submarine Cable" OR "DWDM" OR "Optical Network" OR "Telecommunications Engineer"'
    }
]

# 第一關：職位名稱必須包含以下至少一個字眼
VALID_TITLE_KEYWORDS = [
    'network', 'networking', 'transmission', 'submarine', 'subsea', 'optical', 'fiber', 'fibre',
    'dwdm', 'otn', 'slte', 'telecom', 'telecommunication', 'infrastructure', 'cisco', 'firewall',
    'noc', 'wan', 'lan', 'routing', 'voice', 'datacenter', 'data centre'
]

# 第一關排除：直接踢走軟件開發、機電、銷售等非網絡基建職位
EXCLUDE_TITLE_KEYWORDS = [
    'software', 'frontend', 'front-end', 'backend', 'back-end', 'full stack', 'fullstack',
    'mobile', 'ios', 'android', 'java', 'react', '.net', 'civil', 'mechanical', 'building services',
    'hvac', 'plumbing', 'sales', 'account manager', 'marketing', 'hr ', 'human resources', 'recruiter'
]

# 第二關：核心技術字典 (JD 內文必須至少命中 1 項，否則視為假 Network Post 直接丟棄)
SKILL_PATTERNS = {
    # 路由與交換 (Routing & Switching)
    r'\bBGP\b': 'BGP',
    r'\bOSPF\b': 'OSPF',
    r'\bMPLS\b': 'MPLS',
    r'\bVLAN\w*\b': 'VLAN',
    r'\bSTP\b|\bSpanning Tree\b': 'STP',
    r'\bL2/L3\b|\bLayer 2\b|\bLayer 3\b': 'L2/L3 Routing & Switching',
    r'\bVXLAN\b|\bEVPN\b': 'VXLAN/EVPN',
    r'\bTCP/IP\b|\bDHCP\b|\bDNS\b': 'TCP/IP & Core Services',
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
    # 廣域網與架構 (WAN, Cloud & Operations)
    r'\bSD-WAN\b|\bSDWAN\b': 'SD-WAN',
    r'\bIPsec\b|\bVPN\b|\bGRE\b': 'IPsec/VPN Tunnel',
    r'\bWi-Fi\b|\bWireless\b|\bWLAN\b|\bWLC\b': 'Enterprise Wi-Fi/WLAN',
    r'\bData\s*Cent(?:er|re)\b': 'Data Centre Networking',
    r'\bCutover\b|\bMigration\b': 'Network Migration/Cutover',
    r'\bFirewall\b': 'Firewall Implementation',
    r'\bSolarWinds\b|\bPRTG\b|\bZabbix\b|\bSNMP\b|\bNetFlow\b': 'Network Monitoring (SNMP/NMS)',
    r'\bPython\b|\bAnsible\b|\bNetmiko\b': 'Network Automation (Python/Ansible)',
    r'\bNOC\b|\bIncident\b|\bTroubleshooting\b': 'Network Troubleshooting/NOC',
    # 海底光纜與傳輸技術 (Transmission & Submarine Cable)
    r'\bDWDM\b': 'DWDM',
    r'\bOTN\b': 'OTN',
    r'\bSLTE\b|\bPFE\b': 'Submarine SLTE/PFE',
    r'\bSubmarine\s*Cable\b|\bSubsea\b': 'Submarine Cable Systems',
    r'\bSDH\b|\bSONET\b': 'SDH/SONET',
    r'\bOptical\b|\bFiber\b|\bFibre\b': 'Optical Fiber Transmission',
    r'\bCiena\b|\bInfinera\b|\bNokia\b|\bAlcatel\b': 'Optical Vendor (Ciena/Nokia/Infinera)',
    r'\bROADM\b': 'ROADM',
    r'\bOTDR\b|\bOSA\b|\bBER\b|\bRFC\s*2544\b': 'Optical/Circuit Testing (OTDR/BER)'
}

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

def is_title_valid(title):
    t_lower = title.lower()
    if any(bad in t_lower for bad in EXCLUDE_TITLE_KEYWORDS):
        return False
    return any(good in t_lower for good in VALID_TITLE_KEYWORDS)

def extract_experience(text):
    if not text or not isinstance(text, str):
        return "未列明"
    
    range_match = re.search(
        r'(\d{1,2})\s*(?:-|to|–|~)\s*(\d{1,2})\+?\s*(?:years?|yrs?)',
        text, re.IGNORECASE
    )
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        if 0 < low < high <= 20:
            return f"{low} - {high} 年"

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
    combined = f"{title} {text}" if isinstance(text, str) else str(title)
    found_skills = []
    for pattern, label in SKILL_PATTERNS.items():
        if re.search(pattern, combined, re.IGNORECASE):
            if label not in found_skills:
                found_skills.append(label)
    return found_skills

def extract_certs(text):
    if not text or not isinstance(text, str):
        return "-"
    found_certs = []
    for pattern, label in CERT_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            if label not in found_certs:
                found_certs.append(label)
    return ", ".join(found_certs) if found_certs else "-"

def extract_jd_requirements_snippet(text):
    """提取 JD 內的職責與要求核心段落，自動跳過開頭冗長的公司介紹"""
    if not text or not isinstance(text, str):
        return ""
    clean_text = re.sub(r'\n{3,}', '\n\n', text.strip())
    
    # 擴充常見的職責與要求標題字眼
    match = re.search(
        r'(?:Requirements|Qualifications|What you\'ll need|What we look for|What you will do|Responsibilities|Key Responsibilities|Job Duties|The Role|About the Role|You should have|Skills & Experience)',
        clean_text, re.IGNORECASE
    )
    if match:
        start_idx = max(0, match.start() - 50)
        return clean_text[start_idx:start_idx + 2500]
    
    # 如果完全沒有標準標題，通常開頭 30% 是公司介紹，直接取中後段最核心的 2500 字
    if len(clean_text) > 2500:
        return clean_text[-2500:]
    return clean_text

def fetch_all_jobs(existing_links):
    scraped_rows = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for cfg in SEARCH_CONFIGS:
        cat = cfg["category"]
        term = cfg["term"]
        print(f"[*] 正在搜尋 {cat} 職位: {term}")

        try:
            jobs_df = scrape_jobs(
                site_name=["indeed", "linkedin"],
                search_term=term,
                location="Hong Kong",
                results_wanted=25,
                hours_old=72,
                country_indeed='Hong Kong',
                linkedin_fetch_description=True
            )
            print(f" -> 初步獲取 {len(jobs_df)} 筆職位，開始進行雙重過濾...")

            for _, job in jobs_df.iterrows():
                title = str(job.get("title", "")).strip()
                company = str(job.get("company", "N/A")).strip()
                loc = str(job.get("location", "HK")).strip()
                company_field = f"{company} ({loc})" if loc and loc != "nan" else company

                job_url = str(job.get("job_url", "")).strip()
                if not job_url or job_url == "nan" or job_url in existing_links:
                    continue

                # 第一關：檢查標題是否為真正的 Network / Transmission 職位
                if not is_title_valid(title):
                    print(f"    [跳過 - 標題不符] {title}")
                    continue

                description = str(job.get("description", "")) if pd.notna(job.get("description")) else ""
                
                # 第二關：提取技能並驗證內文是否真的包含 Network/Transmission 技術要求
                matched_skills = extract_skills(description, title)
                if not matched_skills:
                    print(f"    [跳過 - 內文無具體網絡/傳輸技術字眼] {title} | {company}")
                    continue

                key_skills_str = ", ".join(matched_skills[:6])
                exp_req = extract_experience(description)
                certs_req = extract_certs(description)
                raw_jd_snippet = extract_jd_requirements_snippet(description)

                min_sal = str(int(job["min_amount"])) if pd.notna(job.get("min_amount")) else ""
                max_sal = str(int(job["max_amount"])) if pd.notna(job.get("max_amount")) else ""
                site = str(job.get("site", "JobBoard")).capitalize()

                scraped_rows.append([
                    today_str,
                    cat,
                    title,
                    company_field,
                    min_sal,
                    max_sal,
                    exp_req,
                    key_skills_str,
                    certs_req,
                    site,
                    job_url,
                    "Open",
                    raw_jd_snippet  # 第 13 欄 (M 欄)：儲存 JD 原文精華供 Chat Room 分析
                ])
                existing_links.add(job_url)
                print(f"    [OK 入選] {site}: {title} | 年資: {exp_req} | 技能: {key_skills_str}")

        except Exception as e:
            print(f"[!] 搜尋 {cat} 時發生錯誤: {e}")

    return scraped_rows

def main():
    print("[*] 啟動雙重過濾職位爬蟲任務...")
    worksheet = init_google_sheet()

    try:
        existing_links = set(worksheet.col_values(11)[1:])
    except Exception:
        existing_links = set()

    new_rows = fetch_all_jobs(existing_links)
    print(f"[*] 任務抓取完畢，共篩選出 {len(new_rows)} 個高純度職位。")

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
        print(f"[+] 成功寫入 {len(new_rows)} 個職位（含 M 欄 Raw JD 摘要）！")
    else:
        print("[i] 本次未發現符合條件的全新職位。")

if __name__ == "__main__":
    main()
