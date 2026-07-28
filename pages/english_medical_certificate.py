# -*- coding: utf-8 -*-
"""
해외 출국용 영문 진단서/처방전 자동 변환 도구
=============================================

한글 진단서 또는 처방전(스캔/사진)을 업로드하면 Gemini Vision으로 항목을
추출하고, 영문 HTML 문서와 PDF를 함께 생성합니다.

⚠️ 중요: 이 도구가 생성하는 문서는 "영문 번역 초안"입니다.
   반드시 담당 의사(처방전의 경우 약사도 포함)의 검토 및 서명, 병원/약국
   직인 날인 후 제출해야 법적 효력이 있는 공식 서류가 됩니다.
   (이 안내 문구는 모든 출력물에 자동으로 포함됩니다.)

필요 패키지 (requirements.txt):
    streamlit
    google-genai
    pandas
    xhtml2pdf
    reportlab

함께 배포해야 하는 파일 (같은 pages/ 폴더 안):
    diagnosis_mapping.csv
    fonts/NanumGothic-Regular.ttf
    fonts/NanumGothic-Bold.ttf

Gemini API 키는 Streamlit Cloud Secrets에 GEMINI_API_KEY 로 등록하세요.
"""

import os
import io
import json
from datetime import date

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xhtml2pdf import pisa

# ---------------------------------------------------------------------------
# 0. 기본 설정
# ---------------------------------------------------------------------------

st.set_page_config(page_title="영문 진단서/처방전 변환 도구", page_icon="🩺", layout="wide")

BASE_DIR = os.path.dirname(__file__)
MAPPING_CSV_PATH = os.path.join(BASE_DIR, "diagnosis_mapping.csv")
FONT_REGULAR_PATH = os.path.join(BASE_DIR, "fonts", "NanumGothic-Regular.ttf")
FONT_BOLD_PATH = os.path.join(BASE_DIR, "fonts", "NanumGothic-Bold.ttf")

DOC_TYPE_OPTIONS = {
    "certificate": "🩺 진단서 (Medical Certificate)",
    "prescription": "💊 처방전 (Prescription)",
}

PURPOSE_OPTIONS = {
    "fit_to_fly": "✈️ Fit to Fly (비행 적합성 확인서)",
    "insurance": "📄 해외 보험 청구용 진단서",
    "general": "🏥 일반 영문 진단서 (비자/제출용)",
}

DISCLAIMER_TEXT = (
    "This document is an English-language draft translated with the assistance "
    "of an automated tool from the original Korean medical document. "
    "It is NOT valid until reviewed, corrected if necessary, and signed/stamped "
    "by the issuing physician (and pharmacist, for prescriptions) and the "
    "hospital/pharmacy. Please have the responsible medical professional verify "
    "all contents before this document is submitted to any airline, embassy, "
    "insurer, customs authority, or other institution."
)


# ---------------------------------------------------------------------------
# 1. 폰트 등록 (PDF 한글 렌더링용, 최초 1회)
# ---------------------------------------------------------------------------

_FONTS_REGISTERED = False


def ensure_fonts_registered():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    if os.path.exists(FONT_REGULAR_PATH):
        pdfmetrics.registerFont(TTFont("NanumGothic", FONT_REGULAR_PATH))
    if os.path.exists(FONT_BOLD_PATH):
        pdfmetrics.registerFont(TTFont("NanumGothic-Bold", FONT_BOLD_PATH))
    if os.path.exists(FONT_REGULAR_PATH) and os.path.exists(FONT_BOLD_PATH):
        pdfmetrics.registerFontFamily(
            "NanumGothic", normal="NanumGothic", bold="NanumGothic-Bold"
        )
    _FONTS_REGISTERED = True


def html_to_pdf_bytes(html_str: str) -> bytes:
    ensure_fonts_registered()
    out = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(html_str), dest=out)
    if result.err:
        raise RuntimeError("PDF 변환 중 오류가 발생했습니다.")
    return out.getvalue()


# ---------------------------------------------------------------------------
# 2. 병명 매핑 테이블
# ---------------------------------------------------------------------------

@st.cache_data
def load_mapping_table() -> pd.DataFrame:
    empty_df = pd.DataFrame(columns=["kcd_code", "icd10_code", "korean_name", "english_name"])
    if not os.path.exists(MAPPING_CSV_PATH):
        return empty_df
    try:
        return pd.read_csv(MAPPING_CSV_PATH, dtype=str).fillna("")
    except Exception as e:
        st.warning(f"⚠️ 병명 매핑 테이블을 읽는 중 오류가 발생하여 매핑 없이 진행합니다: {e}")
        return empty_df


def lookup_english_diagnosis(korean_name: str, mapping_df: pd.DataFrame) -> str:
    if not korean_name:
        return ""
    match = mapping_df[mapping_df["korean_name"].str.strip() == korean_name.strip()]
    if not match.empty:
        return match.iloc[0]["english_name"]
    contains = mapping_df[mapping_df["korean_name"].apply(lambda x: x in korean_name or korean_name in x)]
    if not contains.empty:
        return contains.iloc[0]["english_name"]
    return ""


# ---------------------------------------------------------------------------
# 3. Gemini Vision 추출 프롬프트
# ---------------------------------------------------------------------------

CERTIFICATE_EXTRACTION_PROMPT = """
당신은 한국 병원에서 발급한 진단서(진단서/소견서) 이미지를 분석하는 전문가입니다.
아래 이미지는 병원마다 서식이 다른 한글 진단서입니다. 서식에 관계없이 다음 항목을
최대한 정확하게 찾아서 순수 JSON 형식으로만 응답하세요. 항목을 찾을 수 없으면
빈 문자열("")로 두세요. 설명, 코드블록 기호(```), 그 외 텍스트는 절대 포함하지 마세요.

{
  "hospital_name": "발급 병원명",
  "patient_name_kor": "환자 성명 (한글)",
  "patient_name_eng": "환자 성명 로마자 표기 시도 (여권 표기가 없다면 추정 표기, 없으면 빈값)",
  "patient_birth_date": "생년월일 (YYYY-MM-DD 형식으로 변환)",
  "patient_gender": "성별 (M/F, 확인 불가시 빈값)",
  "diagnosis_korean": "진단명(병명) 원문 그대로 (한글)",
  "diagnosis_code": "진단서에 명시된 상병코드(KCD/ICD)가 있다면 그대로, 없으면 빈값",
  "onset_date": "발병일 (YYYY-MM-DD)",
  "diagnosis_date": "진단일 (YYYY-MM-DD)",
  "issue_date": "진단서 발급일 (YYYY-MM-DD)",
  "clinical_summary_korean": "치료 경과 및 향후 소견 원문 (한글, 요약하지 말고 원문 최대한 그대로)",
  "doctor_name": "발급 의사 성명",
  "doctor_license_no": "의사 면허번호",
  "hospital_phone": "병원 전화번호 (있는 경우)",
  "hospital_address": "병원 주소 (있는 경우)"
}
"""

PRESCRIPTION_EXTRACTION_PROMPT = """
당신은 한국 병원/의원에서 발급한 처방전 이미지를 분석하는 전문가입니다.
아래 이미지는 병원마다 서식이 다른 한글 처방전입니다. 서식에 관계없이 다음 항목을
최대한 정확하게 찾아서 순수 JSON 형식으로만 응답하세요. 항목을 찾을 수 없으면
빈 문자열("") 또는 빈 배열([])로 두세요. 설명, 코드블록 기호(```), 그 외 텍스트는
절대 포함하지 마세요.

{
  "hospital_name": "발급 병원/의원명",
  "patient_name_kor": "환자 성명 (한글)",
  "patient_name_eng": "환자 성명 로마자 표기 시도 (없으면 빈값)",
  "patient_birth_date": "생년월일 (YYYY-MM-DD)",
  "patient_gender": "성별 (M/F, 확인 불가시 빈값)",
  "prescription_date": "처방일 (YYYY-MM-DD)",
  "issue_date": "처방전 발급일/유효기간 관련 날짜 (YYYY-MM-DD, 있는 경우)",
  "doctor_name": "처방 의사 성명",
  "doctor_license_no": "의사 면허번호",
  "hospital_phone": "병원 전화번호 (있는 경우)",
  "hospital_address": "병원 주소 (있는 경우)",
  "medications": [
    {
      "drug_name_korean": "처방전에 기재된 약품명 원문 (한글/영문 상품명 그대로)",
      "dosage_per_administration": "1회 투여량 (예: 1정, 2캡슐 등 원문 그대로)",
      "frequency_per_day": "1일 투여 횟수 (예: 1일 3회, 원문 그대로)",
      "duration_days": "총 투여일수 (숫자만, 예: 7)",
      "instructions_korean": "복용법/특이사항 원문 (예: 식후 30분, 필요시 복용 등)"
    }
  ]
}

medications는 처방전에 기재된 약품 개수만큼 배열로 모두 포함하세요. 하나도 못 찾으면 빈 배열로 두세요.
"""


def call_gemini_vision_extract(image_bytes: bytes, mime_type: str, api_key: str, prompt: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )

    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        raw_text = raw_text.replace("json", "", 1).strip()
    return json.loads(raw_text)


def call_gemini_translate_diagnosis(korean_diagnosis: str, api_key: str) -> str:
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = (
        "다음은 한국 진단서에 기재된 병명입니다. 국제적으로 통용되는 "
        "영문 의학 진단명(ICD-10 표준 명칭 기준)으로만 답하세요. "
        "다른 설명 없이 영문 병명만 출력하세요.\n\n"
        f"병명: {korean_diagnosis}"
    )
    response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    return response.text.strip()


def call_gemini_translate_free_text(korean_text: str, api_key: str) -> str:
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = (
        "다음은 한국 진단서에 기재된 치료 경과 및 향후 소견 내용입니다. "
        "해외 제출용 영문 진단서에 들어갈 수 있도록, 의무기록에 쓰이는 "
        "격식있고 간결한 영문으로 번역하세요. 번역문만 출력하고 다른 설명은 "
        "하지 마세요.\n\n"
        f"{korean_text}"
    )
    response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    return response.text.strip()


def call_gemini_translate_medications(medications: list, api_key: str) -> list:
    """처방 약품 목록을 한 번에 영문으로 번역. 특히 성분명(제네릭명)을 우선 표기하도록 요청.
    (상품명만 표기하면 해외에서 동일 성분 확인이 어렵기 때문)"""
    from google import genai

    if not medications:
        return []

    client = genai.Client(api_key=api_key)
    prompt = (
        "다음은 한국 처방전에 기재된 약품 목록입니다(JSON 배열). "
        "각 약품에 대해 아래 형식으로 영문 정보를 채워서 JSON 배열로만 응답하세요. "
        "설명이나 코드블록 없이 순수 JSON만 출력하세요.\n\n"
        "약품명은 가능하면 '영문 성분명(제네릭명, INN 기준) (상품명이 확인되면 상품명 병기)' "
        "형식으로 작성하세요. 확실하지 않으면 상품명 로마자 표기만이라도 제공하세요.\n\n"
        "입력:\n"
        f"{json.dumps(medications, ensure_ascii=False)}\n\n"
        "출력 형식 (배열 길이와 순서는 입력과 동일하게 유지):\n"
        "[\n"
        "  {\n"
        '    "drug_name_english": "영문 성분명 (상품명)",\n'
        '    "dosage_frequency_english": "예: 1 tablet, 3 times a day",\n'
        '    "duration_english": "예: 7 days",\n'
        '    "instructions_english": "예: Take after meals"\n'
        "  }\n"
        "]"
    )
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )
    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        raw_text = raw_text.replace("json", "", 1).strip()
    try:
        return json.loads(raw_text)
    except Exception:
        # 번역 실패 시 빈 값으로 채워서 검수 단계에서 수동 입력 가능하도록 함
        return [
            {
                "drug_name_english": "",
                "dosage_frequency_english": "",
                "duration_english": "",
                "instructions_english": "",
            }
            for _ in medications
        ]


# ---------------------------------------------------------------------------
# 4. HTML 문서 생성
# ---------------------------------------------------------------------------

BASE_CSS = """
<style>
    @page { size: A4; margin: 2cm; }
    body {
        font-family: 'NanumGothic';
        font-size: 10.5pt;
        color: #1a1a1a;
        line-height: 1.5;
    }
    h1 {
        font-family: 'NanumGothic-Bold';
        text-align: center;
        font-size: 17pt;
        margin-bottom: 4px;
    }
    .hospital-name {
        text-align: center;
        font-family: 'NanumGothic-Bold';
        font-size: 12pt;
        margin-top: 0;
        margin-bottom: 2px;
    }
    .hospital-sub {
        text-align: center;
        font-size: 9pt;
        color: #444444;
        margin-bottom: 18px;
    }
    table.info-table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 14px;
    }
    table.info-table td {
        border: 1px solid #999999;
        padding: 7px 10px;
        font-size: 10pt;
        vertical-align: top;
    }
    table.info-table td.label {
        font-family: 'NanumGothic-Bold';
        width: 32%;
        background-color: #f2f2f2;
    }
    .section-heading {
        font-family: 'NanumGothic-Bold';
        font-size: 11.5pt;
        margin-top: 16px;
        margin-bottom: 6px;
    }
    .med-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 6px;
    }
    .med-table th, .med-table td {
        border: 1px solid #999999;
        padding: 6px 8px;
        font-size: 9.5pt;
        text-align: left;
    }
    .med-table th {
        font-family: 'NanumGothic-Bold';
        background-color: #f2f2f2;
    }
    .sign-table {
        width: 100%;
        margin-top: 28px;
    }
    .sign-table td {
        font-size: 10pt;
        padding-top: 18px;
        vertical-align: top;
    }
    .notice-heading {
        font-family: 'NanumGothic-Bold';
        font-size: 9pt;
        margin-top: 24px;
    }
    .notice-body {
        font-size: 8pt;
        font-style: italic;
        color: #444444;
    }
    .italic-note {
        font-style: italic;
        font-size: 9.5pt;
        color: #333333;
    }
</style>
"""


def _row(label: str, value: str) -> str:
    value = value if value else "-"
    return f'<tr><td class="label">{label}</td><td>{value}</td></tr>'


def generate_certificate_html(data: dict, purpose: str) -> str:
    title_map = {
        "fit_to_fly": "MEDICAL CERTIFICATE OF FITNESS TO FLY",
        "insurance": "MEDICAL CERTIFICATE FOR INSURANCE CLAIM",
        "general": "MEDICAL CERTIFICATE",
    }
    title = title_map.get(purpose, "MEDICAL CERTIFICATE")

    diagnosis_line = data.get("diagnosis_english", "")
    if data.get("diagnosis_code"):
        diagnosis_line = f'{diagnosis_line} (Code: {data.get("diagnosis_code")})'

    fit_to_fly_block = ""
    if purpose == "fit_to_fly":
        fit_to_fly_block = f"""
        <div class="section-heading">Fitness to Fly Statement</div>
        <p class="italic-note">
            Based on the current clinical condition described above, the patient is
            considered ______________ (fit / fit with conditions / not fit) to travel
            by air as of the date of this certificate.
            [병원에서 해당 사항에 체크 또는 문구 수정 필요]
        </p>
        """

    html = f"""
    <html><head>{BASE_CSS}</head><body>
        <h1>{title}</h1>
        <p class="hospital-name">{data.get('hospital_name', '')}</p>
        <p class="hospital-sub">{data.get('hospital_address', '')} &nbsp; {data.get('hospital_phone', '')}</p>

        <table class="info-table">
            {_row("Patient Name", data.get("patient_name_eng") or data.get("patient_name_kor", ""))}
            {_row("Date of Birth", data.get("patient_birth_date", ""))}
            {_row("Gender", data.get("patient_gender", ""))}
        </table>

        <table class="info-table">
            {_row("Diagnosis", diagnosis_line)}
            {_row("Date of Onset", data.get("onset_date", ""))}
            {_row("Date of Diagnosis", data.get("diagnosis_date", ""))}
            {_row("Date of Issue", data.get("issue_date", ""))}
        </table>

        <div class="section-heading">Clinical Summary / Recommendation</div>
        <p>{data.get("clinical_summary_english", "")}</p>

        {fit_to_fly_block}

        <table class="sign-table">
            <tr>
                <td>Physician: {data.get('doctor_name', '')}</td>
                <td>Signature: ______________________</td>
            </tr>
            <tr>
                <td>License No.: {data.get('doctor_license_no', '')}</td>
                <td>Hospital Stamp:</td>
            </tr>
        </table>

        <div class="notice-heading">Notice</div>
        <p class="notice-body">{DISCLAIMER_TEXT}</p>
    </body></html>
    """
    return html


def generate_prescription_html(data: dict) -> str:
    med_rows = ""
    for med in data.get("medications", []):
        med_rows += f"""
        <tr>
            <td>{med.get('drug_name_english', '') or '-'}</td>
            <td>{med.get('drug_name_korean', '') or '-'}</td>
            <td>{med.get('dosage_frequency_english', '') or '-'}</td>
            <td>{med.get('duration_english', '') or '-'}</td>
            <td>{med.get('instructions_english', '') or '-'}</td>
        </tr>
        """

    if not med_rows:
        med_rows = '<tr><td colspan="5">No medication information extracted.</td></tr>'

    html = f"""
    <html><head>{BASE_CSS}</head><body>
        <h1>PRESCRIPTION</h1>
        <p class="hospital-name">{data.get('hospital_name', '')}</p>
        <p class="hospital-sub">{data.get('hospital_address', '')} &nbsp; {data.get('hospital_phone', '')}</p>

        <table class="info-table">
            {_row("Patient Name", data.get("patient_name_eng") or data.get("patient_name_kor", ""))}
            {_row("Date of Birth", data.get("patient_birth_date", ""))}
            {_row("Gender", data.get("patient_gender", ""))}
            {_row("Prescription Date", data.get("prescription_date", ""))}
            {_row("Date of Issue", data.get("issue_date", ""))}
        </table>

        <div class="section-heading">Medications</div>
        <table class="med-table">
            <tr>
                <th>Drug (Generic / Brand)</th>
                <th>Original Name (Korean)</th>
                <th>Dosage &amp; Frequency</th>
                <th>Duration</th>
                <th>Instructions</th>
            </tr>
            {med_rows}
        </table>

        <table class="sign-table">
            <tr>
                <td>Prescribing Physician: {data.get('doctor_name', '')}</td>
                <td>Signature: ______________________</td>
            </tr>
            <tr>
                <td>License No.: {data.get('doctor_license_no', '')}</td>
                <td>Hospital/Pharmacy Stamp:</td>
            </tr>
        </table>

        <div class="notice-heading">Notice</div>
        <p class="notice-body">{DISCLAIMER_TEXT}</p>
    </body></html>
    """
    return html


# ---------------------------------------------------------------------------
# 5. Streamlit UI
# ---------------------------------------------------------------------------

def main():
    st.title("🩺 해외 출국용 영문 진단서 / 처방전 변환 도구")
    st.caption(
        "한글 진단서 또는 처방전을 업로드하면 AI가 항목을 추출하고, 검토 후 영문 HTML/PDF 초안을 생성합니다. "
        "생성된 문서는 반드시 담당 의사(및 약사)의 확인과 서명·직인 날인 후 제출해야 합니다."
    )

    api_key = st.secrets.get("GEMINI_API_KEY", "") if hasattr(st, "secrets") else ""
    if not api_key:
        api_key = st.text_input("Gemini API Key (테스트용, 배포 시에는 Secrets에 등록하세요)", type="password")

    mapping_df = load_mapping_table()

    if "extracted" not in st.session_state:
        st.session_state.extracted = None
    if "doc_type" not in st.session_state:
        st.session_state.doc_type = None

    st.divider()
    st.subheader("1단계: 문서 종류 선택")
    doc_type_label = st.radio("어떤 문서를 변환하나요?", list(DOC_TYPE_OPTIONS.values()))
    doc_type_key = [k for k, v in DOC_TYPE_OPTIONS.items() if v == doc_type_label][0]

    purpose_key = "general"
    if doc_type_key == "certificate":
        st.subheader("1-1단계: 용도 선택")
        purpose_label = st.radio("이 영문 진단서는 어떤 용도로 제출하나요?", list(PURPOSE_OPTIONS.values()))
        purpose_key = [k for k, v in PURPOSE_OPTIONS.items() if v == purpose_label][0]

    st.divider()
    label = "진단서" if doc_type_key == "certificate" else "처방전"
    st.subheader(f"2단계: 한글 {label} 업로드")
    uploaded_file = st.file_uploader(
        f"{label} 스캔본/사진 업로드 (JPG, PNG 권장)", type=["jpg", "jpeg", "png"], key="uploader"
    )

    if uploaded_file is not None:
        st.image(uploaded_file, caption=f"업로드된 {label}", width=350)

        if st.button("🔍 AI로 항목 추출하기", type="primary", disabled=not api_key):
            with st.spinner("Gemini Vision으로 분석하는 중입니다..."):
                try:
                    image_bytes = uploaded_file.getvalue()
                    mime_type = uploaded_file.type or "image/jpeg"

                    if doc_type_key == "certificate":
                        extracted = call_gemini_vision_extract(
                            image_bytes, mime_type, api_key, CERTIFICATE_EXTRACTION_PROMPT
                        )
                        korean_diag = extracted.get("diagnosis_korean", "")
                        eng_diag = lookup_english_diagnosis(korean_diag, mapping_df)
                        if not eng_diag and korean_diag:
                            eng_diag = call_gemini_translate_diagnosis(korean_diag, api_key)
                            extracted["diagnosis_mapped_from"] = "AI_translation"
                        else:
                            extracted["diagnosis_mapped_from"] = "mapping_table" if eng_diag else "none"
                        extracted["diagnosis_english"] = eng_diag

                        if extracted.get("clinical_summary_korean"):
                            extracted["clinical_summary_english"] = call_gemini_translate_free_text(
                                extracted["clinical_summary_korean"], api_key
                            )
                        else:
                            extracted["clinical_summary_english"] = ""

                    else:  # prescription
                        extracted = call_gemini_vision_extract(
                            image_bytes, mime_type, api_key, PRESCRIPTION_EXTRACTION_PROMPT
                        )
                        meds = extracted.get("medications", [])
                        translations = call_gemini_translate_medications(meds, api_key)
                        for i, med in enumerate(meds):
                            t = translations[i] if i < len(translations) else {}
                            med["drug_name_english"] = t.get("drug_name_english", "")
                            med["dosage_frequency_english"] = t.get("dosage_frequency_english", "")
                            med["duration_english"] = t.get("duration_english", "")
                            med["instructions_english"] = t.get("instructions_english", "")
                        extracted["medications"] = meds

                    st.session_state.extracted = extracted
                    st.session_state.doc_type = doc_type_key
                    st.session_state.purpose = purpose_key
                    st.success("추출 완료! 아래에서 내용을 확인 및 수정해주세요.")
                except Exception as e:
                    st.error(f"추출 중 오류가 발생했습니다: {e}")

    if st.session_state.extracted and st.session_state.doc_type == doc_type_key:
        data = st.session_state.extracted
        st.divider()
        st.subheader("3단계: 추출 결과 검수 (필수)")

        if doc_type_key == "certificate":
            if data.get("diagnosis_mapped_from") == "AI_translation":
                st.warning(
                    "⚠️ 이 병명은 매핑 테이블에 없어 AI가 직접 번역했습니다. "
                    "정확한 진단명인지 반드시 의료진이 재확인해주세요."
                )

            col1, col2 = st.columns(2)
            with col1:
                data["hospital_name"] = st.text_input("병원명", data.get("hospital_name", ""))
                data["patient_name_eng"] = st.text_input(
                    "환자 성명 영문 (여권 표기 기준 확인 필요)",
                    data.get("patient_name_eng") or data.get("patient_name_kor", ""),
                )
                data["patient_birth_date"] = st.text_input("생년월일 (YYYY-MM-DD)", data.get("patient_birth_date", ""))
                data["patient_gender"] = st.selectbox(
                    "성별", ["", "M", "F"],
                    index=["", "M", "F"].index(data.get("patient_gender", "")) if data.get("patient_gender") in ["", "M", "F"] else 0,
                )
                data["doctor_name"] = st.text_input("발급 의사 성명", data.get("doctor_name", ""))
                data["doctor_license_no"] = st.text_input("의사 면허번호", data.get("doctor_license_no", ""))
            with col2:
                data["diagnosis_english"] = st.text_input("진단명 영문 (확인 필수)", data.get("diagnosis_english", ""))
                data["diagnosis_code"] = st.text_input("진단 코드 (선택)", data.get("diagnosis_code", ""))
                data["onset_date"] = st.text_input("발병일 (YYYY-MM-DD)", data.get("onset_date", ""))
                data["diagnosis_date"] = st.text_input("진단일 (YYYY-MM-DD)", data.get("diagnosis_date", ""))
                data["issue_date"] = st.text_input("발급일 (YYYY-MM-DD)", data.get("issue_date", str(date.today())))
                data["hospital_address"] = st.text_input("병원 주소 (선택)", data.get("hospital_address", ""))
                data["hospital_phone"] = st.text_input("병원 전화번호 (선택)", data.get("hospital_phone", ""))

            data["clinical_summary_english"] = st.text_area(
                "치료 경과 및 향후 소견 (영문, 확인 및 수정 필요)",
                data.get("clinical_summary_english", ""),
                height=150,
            )

            with st.expander("원문(한글) 추출 결과 보기"):
                st.json({
                    "diagnosis_korean": data.get("diagnosis_korean", ""),
                    "clinical_summary_korean": data.get("clinical_summary_korean", ""),
                })

        else:  # prescription
            col1, col2 = st.columns(2)
            with col1:
                data["hospital_name"] = st.text_input("병원명", data.get("hospital_name", ""))
                data["patient_name_eng"] = st.text_input(
                    "환자 성명 영문",
                    data.get("patient_name_eng") or data.get("patient_name_kor", ""),
                )
                data["patient_birth_date"] = st.text_input("생년월일 (YYYY-MM-DD)", data.get("patient_birth_date", ""))
                data["patient_gender"] = st.selectbox(
                    "성별", ["", "M", "F"],
                    index=["", "M", "F"].index(data.get("patient_gender", "")) if data.get("patient_gender") in ["", "M", "F"] else 0,
                )
            with col2:
                data["prescription_date"] = st.text_input("처방일 (YYYY-MM-DD)", data.get("prescription_date", ""))
                data["issue_date"] = st.text_input("발급일 (YYYY-MM-DD)", data.get("issue_date", str(date.today())))
                data["doctor_name"] = st.text_input("처방 의사 성명", data.get("doctor_name", ""))
                data["doctor_license_no"] = st.text_input("의사 면허번호", data.get("doctor_license_no", ""))
                data["hospital_address"] = st.text_input("병원 주소 (선택)", data.get("hospital_address", ""))
                data["hospital_phone"] = st.text_input("병원 전화번호 (선택)", data.get("hospital_phone", ""))

            st.markdown("**약품 목록 (확인 및 수정 필요 — 특히 성분명 영문 표기)**")
            st.caption("⚠️ 약품 영문명은 AI 번역 결과입니다. 반드시 의사/약사가 정확한 성분명(INN)인지 확인해주세요.")

            meds = data.get("medications", [])
            for i, med in enumerate(meds):
                with st.expander(f"약품 {i+1}: {med.get('drug_name_korean', '(이름 미확인)')}", expanded=True):
                    med["drug_name_korean"] = st.text_input("원문 약품명 (한글)", med.get("drug_name_korean", ""), key=f"med_kor_{i}")
                    med["drug_name_english"] = st.text_input("영문 성분명/상품명", med.get("drug_name_english", ""), key=f"med_eng_{i}")
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        med["dosage_frequency_english"] = st.text_input("용법·용량 (영문)", med.get("dosage_frequency_english", ""), key=f"med_freq_{i}")
                    with c2:
                        med["duration_english"] = st.text_input("투약 기간 (영문)", med.get("duration_english", ""), key=f"med_dur_{i}")
                    with c3:
                        med["instructions_english"] = st.text_input("복용 지침 (영문)", med.get("instructions_english", ""), key=f"med_inst_{i}")
            data["medications"] = meds

        st.session_state.extracted = data

        st.divider()
        st.subheader("4단계: 영문 문서 생성 (HTML + PDF)")

        if st.button("📄 영문 문서 생성", type="primary"):
            if doc_type_key == "certificate":
                html_str = generate_certificate_html(data, purpose_key)
                base_name = "english_medical_certificate"
            else:
                html_str = generate_prescription_html(data)
                base_name = "english_prescription"

            try:
                pdf_bytes = html_to_pdf_bytes(html_str)
            except Exception as e:
                pdf_bytes = None
                st.error(f"PDF 생성 중 오류가 발생했습니다 (HTML은 정상 생성됨): {e}")

            name_part = (data.get("patient_name_eng") or "patient").replace(" ", "_")
            filename_base = f"{base_name}_{name_part}"

            st.markdown("#### 미리보기")
            components.html(html_str, height=700, scrolling=True)

            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                st.download_button(
                    "⬇️ HTML 다운로드",
                    data=html_str.encode("utf-8"),
                    file_name=f"{filename_base}.html",
                    mime="text/html",
                )
            with col_dl2:
                if pdf_bytes:
                    st.download_button(
                        "⬇️ PDF 다운로드",
                        data=pdf_bytes,
                        file_name=f"{filename_base}.pdf",
                        mime="application/pdf",
                    )

            st.info(
                "생성된 문서는 초안입니다. 담당 의사(및 약사)의 검토·서명과 "
                "병원/약국 직인 날인 후 제출해주세요."
            )


if __name__ == "__main__":
    main()
