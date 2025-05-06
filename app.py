from flask import Flask, render_template, request
import os, requests, zipfile, re, xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime
from collections import defaultdict
from docx import Document

def extract_header_text(docx_path):
    with zipfile.ZipFile(docx_path) as docx:
        header_str = ""
        with docx.open("word/header1.xml") as f:
            tree = ET.parse(f)
            root = tree.getroot()
            for elem in root.iter():
                if elem.tag.endswith('}t') and elem.text:
                    header_str += elem.text.strip()

    field_map = {
        "FAKÜLTESİ": "faculty",
        "BÖLÜMÜ": "department",
        "PROGRAMI": "program",
        "TC KİMLİK NO": "tc_id",
        "ÖĞRENCİ NUMARASI": "student_id",
        "ADI SOYADI": "name",
        "KAYIT TARİHİ": "start_date",
        "AYRILIŞ TARİHİ": "end_date",
        "TRANSKRİPT": "transcript"
    }

    pattern = f"({'|'.join(re.escape(k) for k in field_map.keys())})"
    parts = re.split(pattern, header_str)

    results = {}
    for i in range(1, len(parts)-1, 2):
        key_tr = parts[i].strip()
        value = parts[i+1].strip()

        if not value or key_tr.upper() == "TRANSKRİPT":
            continue

        key_en = field_map.get(key_tr, key_tr.lower().replace(" ", ""))
        results[key_en] = value

    return results

def extract_text_nodes_as_string(docx_path):
    with zipfile.ZipFile(docx_path) as docx:
        with docx.open("word/document.xml") as f:
            tree = ET.parse(f)
            root = tree.getroot()

    text_parts = []

    def recursive_walk(element):
        if element.tag.endswith('}t'):
            text = element.text
            if text and text.strip():
                text_parts.append(text.strip())
        for child in element:
            recursive_walk(child)

    recursive_walk(root)
    return '\n'.join(text_parts)


def parse_semester_text(raw_text):
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    term_pattern = re.compile(r"^\d+\. Yarıyıl$")
    float_pattern = re.compile(r"^\d+\.\d+$")
    grade_pattern = re.compile(r"^[A-Z]{1,2}$|^YT$|^YZ$|^DVZ$|^FD$|^FF$")

    current_term = None
    result = defaultdict(list)
    i = 0

    while i < len(lines):
        line = lines[i]

        if term_pattern.match(line):
            current_term = line
            i += 1
            continue

        if re.match(r'^[A-ZÇĞİÖŞÜ]{2,4}\s?\d{3}$', line) and current_term:
            course_code = line
            course_name = []
            j = i + 1
            while j < len(lines) and not float_pattern.match(lines[j]):
                course_name.append(lines[j])
                j += 1

            credit = lines[j] if j < len(lines) else ''
            akts = lines[j+1] if j+1 < len(lines) else ''
            grade = lines[j+2] if j+2 < len(lines) and grade_pattern.match(lines[j+2]) else ''

            full_name = ' '.join(course_name)

            course_obj = {
                "code": course_code.replace(" ", "").replace("İ", "I"),
                "name": full_name,
                "credit": credit,
                "ects": akts,
                "grade": grade
            }

            result[current_term[0]].append(course_obj)
            i = j + 3
        else:
            i += 1

    return dict(result)

def detectBologna(startDate):
    date_obj = datetime.strptime(startDate, "%d.%m.%Y")

    if int(date_obj.month) > 7:
        return f"{date_obj.year} - {date_obj.year + 1}"
    else:
        return f"{date_obj.year - 1} - {date_obj.year}"

def fetchCurriculum(years_str):
    cookies = {}

    response = requests.get("https://ebs.duzce.edu.tr/tr-TR/Bolum/OgretimProgrami/14?bot=14")
    match = re.search(r'cookiesession1=([A-F0-9]+)', response.headers.get("Set-Cookie"))
    if match:
        cookies["cookiesession1"] = match.group(1)

    years = {}
    for option in BeautifulSoup(response.text, "html.parser").select("select#BolognaYil option"):
        years[option.text.strip()] = option["value"]

    response = requests.post("https://ebs.duzce.edu.tr/tr-TR/Home/BolognaYilGuncelle", json = {
        "yilNo": years[years_str],
        "returnURL": "/tr-TR/Bolum/OgretimProgrami/14?bot=14"
    }, cookies = cookies, allow_redirects = False)
    match = re.search(r'ASP\.NET_SessionId=([^;]+)', response.headers.get("Set-Cookie"))
    if match:
        cookies["ASP.NET_SessionId"] = match.group(1)

    response = requests.get("https://ebs.duzce.edu.tr/tr-TR/Bolum/OgretimProgrami/14?bot=14", cookies = cookies)
    return response.text

def parse_course_tables(html):
    soup = BeautifulSoup(html, "html.parser")
    results = defaultdict(list)
    semesterMap = {}

    for box in soup.select(".ibox"):
        box_title = box.select_one(".ibox-title h5")
        base_title = box_title.get_text(strip=True) if box_title else "Bilinmeyen Yarıyıl"

        content_div = box.select_one(".ibox-content")
        if not content_div:
            continue

        # İçindeki tüm tabloları sırayla al: ilk tablo zorunlu, sonra gelenler seçmeliler olabilir
        tables = content_div.select("table.table")
        table_titles = [base_title]

        # Seçmeli tablo varsa, onun başlığını yukarıdaki div'den al
        for extra in content_div.select("div[style*='background-color']"):
            sel_title = extra.get_text(strip=True)
            if sel_title:
                table_titles.append(sel_title)

        for idx, table in enumerate(tables):
            title = table_titles[idx] if idx < len(table_titles) else f"{base_title} Tablo {idx+1}"

            headers = [th.get_text(strip=True) for th in table.select("thead th")]
            for row in table.select("tbody tr"):
                cells = [td.get_text(strip=True) for td in row.select("td")]
                if len(cells) == len(headers):
                    course_info = dict(zip(headers, cells))
                    if "Seçmeli" in title:
                        results[f"S{title[0]}"].append(course_info)
                        if course_info["Kodu"] in semesterMap:
                            semesterMap[course_info["Kodu"]] += f"S{title[0]}"
                            course_info["Semester"] = f"S{title[0]}"
                        else:
                            semesterMap[course_info["Kodu"]] = f"S{title[0]}"
                            course_info["Semester"] = f"S{title[0]}"
                    else:
                        course_info["Semester"] = title[0]
                        results[title[0]].append(course_info)
                        semesterMap[course_info["Kodu"]] = title[0]

    return dict(results), semesterMap

def kalanHesapla(transcript_file):
    transcript = parse_semester_text(extract_text_nodes_as_string(transcript_file))

    transcript_dersleri = {}
    for semester in transcript:
        for course in transcript[semester]:
            transcript_dersleri[course["code"]] = course["grade"]

    passed = {}
    for semester in transcript:
        for course in transcript[semester]:
            if course["grade"] not in ["FF", "FD", "DVZ", "YZ"]:
                passed[course["code"]] = course

    student = extract_header_text(transcript_file)
    bologna = detectBologna(student["start_date"])

    curriculum, semesterMap = parse_course_tables(fetchCurriculum(bologna))

    remaining = []
    for semester in curriculum:
        if semester[0] != "S":
            for course in curriculum[semester]:
                if course["Kodu"] not in passed:
                    remaining.append(course)

    for course in remaining:
        if course["Zorunlu mu?"] == "Hayır":
            semesterNo = f"S{course['Kodu'][-3]}"
            credits = float(course["Kredi"])

            for p in passed:
                if semesterNo in semesterMap[p] and "used" not in passed[p] and credits > 0:
                    credits -= float(passed[p]["credit"])
                    passed[p]["used"] = True

            course["Kalan Kredi"] = int(credits)
    
    remaining = list(filter(lambda course: course["Zorunlu mu?"] == "Evet" or course["Kalan Kredi"] > 0, remaining))

    remaining = list(map(lambda course: {
    "code": course["Kodu"],
    "name": course["Ders Adı"],
    "is_must": course["Zorunlu mu?"] == "Evet",
    "credit": int(course["Kredi"]),
    "ects": int(course["AKTS"]),
    "semester": int(course["Semester"]),
    "grade": transcript_dersleri.get(course["Kodu"], "-")
} if course["Zorunlu mu?"] == "Evet" else {
    "code": course["Kodu"],
    "name": course["Ders Adı"],
    "is_must": course["Zorunlu mu?"] == "Evet",
    "credit": int(course["Kredi"]),
    "ects": int(course["AKTS"]),
    "semester": int(course["Semester"]),
    "remaining_credits": course["Kalan Kredi"],
    "grade": transcript_dersleri.get(course["Kodu"], "-")
}, remaining))

    return remaining
#GNO hesaplama fonk.
def check_gno(transcript_text):
    lines = transcript_text.splitlines()
    gno = None

    for line in reversed(lines):  # sondan başa arıyoruz çünkü GNO genelde sonlarda olur
        if line.strip().replace(',', '.').replace(' ', '').replace('\t', '').replace("O", "0").count('.') == 1:
            try:
                num = float(line.strip().replace(',', '.'))
                if 0.0 <= num <= 4.0:
                    gno = num
                    break
            except:
                continue

    warnings = []
    if gno is not None and gno < 2.5:
        warnings.append(f"Genel Not Ortalamanız {gno:.2f} olduğu için mezun olamazsınız.")
    elif gno is None:
        warnings.append("Genel Not Ortalaması bulunamadı.")
    
    return warnings

def check_akts_by_semester(transcript_text):
    warnings = []
    # Dönemleri sırayla bul
    semesters = re.split(r'(\d+\. Yarıyıl)', transcript_text)
    combined = []
    
    # "1. Yarıyıl", "2. Yarıyıl" başlıklarıyla içeriği birleştir
    for i in range(1, len(semesters), 2):
        semester_title = semesters[i]
        semester_content = semesters[i+1]
        combined.append((semester_title.strip(), semester_content))

    for title, content in combined:
        # "Dönem Sonu"ndan sonra gelen satırları bul
        match = re.search(r'Dönem Sonu\s*([\d.,]+)\s*([\d.,]+)\s*([\d.,]+)\s*([\d.,]+)', content)
        if match:
            akts = float(match.group(2).replace(',', '.'))
            if akts < 30.0:
                warnings.append(f"{title} toplam AKTS'niz {akts} olduğu için mezun olamazsınız.")
        else:
            print(f"[DEBUG] AKTS bulunamadı: {title}")
    
    print("[DEBUG] AKTS uyarıları:", warnings)
    
    return warnings




app = Flask(__name__)

# Yüklenen dosyanın kaydedileceği dizin
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Dosya türlerini sınırlamak isterseniz
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx'}

# Dosya uzantılarını kontrol eden fonksiyon
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Anasayfa route'u
@app.route('/')
def index():
    return render_template('upload.html')

# Dosya yükleme işlemi
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return 'Dosya seçmediniz!', 400

    file = request.files['file']

    if file.filename == '':
        return 'Dosya seçmediniz!', 400

    if file and allowed_file(file.filename):
        filename = file.filename
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Transkript metnini al
        raw_text = extract_text_nodes_as_string(file_path)

        # Orijinal dosya adını al (uzantısı hariç)
        original_filename = os.path.splitext(file.filename)[0]
        txt_filename = f"{original_filename}.txt"

        # Metni .txt dosyasına kaydet
        txt_path = os.path.join(app.config['UPLOAD_FOLDER'], txt_filename)
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(raw_text)
        

        kalanlar = kalanHesapla(file_path)

        # GNO kontrolünü yap
        gno_warnings = check_gno(raw_text)
        akts_warnings = check_akts_by_semester(raw_text)
        warnings = gno_warnings + akts_warnings

        html = """
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Kalan Dersler</title>
                <style>
                    table {
                        border-collapse: collapse;
                        width: 90%;
                        margin: 20px auto;
                    }
                    th, td {
                        border: 1px solid #aaa;
                        padding: 10px;
                        text-align: center;
                    }
                    th {
                        background-color: #444;
                        color: white;
                    }
                    tr:nth-child(even) {
                        background-color: #f2f2f2;
                    }
                    }
                </style>
            </head>
            <body>
            <h2 style="text-align: center;">Kalan Dersler</h2>
            <table>
                <tr>
                    <th>Kod</th>
                    <th>İsim</th>
                    <th>Zorunlu mu?</th>
                    <th>Kredi</th>
                    <th>ECTS</th>
                    <th>Dönem</th>
                    <th>Kalan Kredi</th>
                    <th>Not Durumu</th>
                </tr>
            """

        for d in kalanlar:
            html += "<tr>"
            html += f"<td>{d.get('code')}</td>"
            html += f"<td>{d.get('name')}</td>"
            html += f"<td>{'Evet' if d.get('is_must') else 'Hayır'}</td>"
            html += f"<td>{d.get('credit')}</td>"
            html += f"<td>{d.get('ects')}</td>"
            html += f"<td>{d.get('semester')}</td>"
            html += f"<td>{d.get('remaining_credits', '-')}</td>"
            html += f"<td>{d.get('grade', '-')}</td>"
            html += "</tr>"

        html += "</table>"

        # Uyarıları ekle
        html += "<div style='width: 90%; margin: 20px auto;'>"
        html += "<h3>Uyarılar</h3>"
        if warnings:
            html += "<ul>"
            for warning in warnings:
                html += f"<li>{warning}</li>"
            html += "</ul>"
        else:
            html += "<p>Genel Not Ortalamanız mezuniyet için yeterlidir.</p>"
        html += "</div>"

        html += "</body></html>"

        return html
    else:
        return 'Geçersiz dosya türü!', 400

if __name__ == '__main__':
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)  # Dosya yükleme klasörünü oluştur
    app.run(debug=True)
