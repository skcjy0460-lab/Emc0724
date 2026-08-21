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
DRUG_MAPPING_CSV_PATH = os.path.join(BASE_DIR, "drug_mapping.csv")
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


@st.cache_data
def load_drug_mapping_table() -> pd.DataFrame:
    """약품 매핑 테이블. 병원에서 자주 처방하는 약품을 미리 등록해두면
    AI 번역보다 훨씬 정확하고 빠르게 매칭된다.
    (기존에 구축하신 '약품 Master DB' 데이터를 이 형식으로 내보내서 사용하시면
    가장 정확도가 높습니다 — item_seq/성분명 조회 결과를 korean_name/english_name/
    ingredient_english 컬럼에 맞춰 변환하시면 됩니다.)"""
    empty_df = pd.DataFrame(columns=["korean_name", "english_name", "ingredient_english"])
    if not os.path.exists(DRUG_MAPPING_CSV_PATH):
        return empty_df
    try:
        return pd.read_csv(DRUG_MAPPING_CSV_PATH, dtype=str).fillna("")
    except Exception as e:
        st.warning(f"⚠️ 약품 매핑 테이블을 읽는 중 오류가 발생하여 매핑 없이 진행합니다: {e}")
        return empty_df


def lookup_english_drug(korean_name: str, mapping_df: pd.DataFrame) -> dict:
    """정확히 일치하거나 포함 관계인 약품명을 매핑 테이블에서 찾는다.
    공백 유무 등 사소한 표기 차이도 허용하기 위해 공백을 제거한 정규화 비교도 시도한다.
    찾으면 {"english_name": ..., "matched": True}, 없으면 {"matched": False}."""
    if not korean_name or mapping_df.empty:
        return {"matched": False}

    match = mapping_df[mapping_df["korean_name"].str.strip() == korean_name.strip()]
    if not match.empty:
        return {"english_name": match.iloc[0]["english_name"], "matched": True}

    contains = mapping_df[mapping_df["korean_name"].apply(lambda x: x and (x in korean_name or korean_name in x))]
    if not contains.empty:
        return {"english_name": contains.iloc[0]["english_name"], "matched": True}

    # 공백 제거 후 정규화 비교 (OCR/AI 추출 시 공백 유무가 달라지는 경우 대응)
    norm_target = korean_name.replace(" ", "")
    norm_series = mapping_df["korean_name"].str.replace(" ", "", regex=False)
    norm_match = mapping_df[norm_series == norm_target]
    if not norm_match.empty:
        return {"english_name": norm_match.iloc[0]["english_name"], "matched": True}
    norm_contains = mapping_df[norm_series.apply(lambda x: x and (x in norm_target or norm_target in x))]
    if not norm_contains.empty:
        return {"english_name": norm_contains.iloc[0]["english_name"], "matched": True}

    return {"matched": False}


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
  "insurance_type_korean": "체크된 보험 종류 (의료보험/의료보호/산재보험/자동차보험/기타 중 표시된 것 원문 그대로, 없으면 빈값)",
  "institution_code": "요양기관기호",
  "hospital_name": "발급 병원/의원명 (의료기관명칭)",
  "hospital_phone": "병원 전화번호",
  "hospital_fax": "병원 팩스번호 (있는 경우)",
  "hospital_email": "병원 e-mail 주소 (있는 경우)",
  "hospital_address": "병원 주소 (있는 경우, 서식에 없으면 빈값)",
  "patient_name_kor": "환자 성명 (한글)",
  "patient_name_eng": "환자 성명 로마자 표기 시도 (없으면 빈값)",
  "patient_birth_date": "환자 주민등록번호 앞 6자리를 생년월일로 환산 (YYYY-MM-DD), 주민등록번호가 전체 노출되어 있어도 생년월일만 추출하고 나머지 번호는 절대 포함하지 마세요",
  "patient_gender": "성별 (M/F, 확인 불가시 빈값)",
  "diagnosis_classification_code": "질병분류기호 (있는 경우만, 환자 요청으로 미기재시 빈값)",
  "doctor_name": "처방의료인의 성명",
  "license_type": "면허종별 (예: 의사/치과의사/한의사)",
  "doctor_license_no": "면허번호",
  "prescription_date": "교부일자 (YYYY-MM-DD)",
  "issue_date": "교부일자와 동일하면 같은 값 (YYYY-MM-DD)",
  "validity_days": "사용기간 - 교부일로부터 며칠간인지 숫자만 (기재 없으면 빈값)",
  "injection_details_korean": "주사제 처방내역 원문 (있는 경우, 원내처방/원외처방 여부 포함)",
  "dispensing_notes_korean": "조제시 참고사항 원문 (있는 경우)",
  "medications": [
    {
      "drug_name_korean": "처방 의약품의 명칭 원문 (한글/영문 상품명 그대로)",
      "dosage_per_administration": "1회 투약량 (원문 그대로, 예: 1정)",
      "frequency_per_day": "1일 투여횟수 (원문 그대로, 예: 3회)",
      "duration_days": "총 투약일수 (숫자만, 예: 7)",
      "substitution_allowed_korean": "대체가능 여부 원문 (예: 가능/불가, 표시 없으면 빈값)",
      "usage_timing_korean": "용법 - 식전/식후/취침전 등 복용 시점 원문",
      "instructions_korean": "분복용 등 기타 복용 특이사항 원문"
    }
  ]
}

medications는 처방전에 기재된 약품 개수만큼 배열로 모두 포함하세요. 하나도 못 찾으면 빈 배열로 두세요.
patient_birth_date를 만들 때 주민등록번호 뒷자리(성별/개인식별 정보)는 절대 결과에 포함하지 마세요.
"""


GEMINI_MODEL = "gemini-3.7-flash"


def _high_thinking_config(**kwargs):
    """모든 의료문서 추출/번역 호출에 공통 적용할 설정. thinking_level을 최대치로
    설정해 정확도를 우선시한다 (속도/비용보다 품질 우선)."""
    from google.genai import types

    return types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="high"),
        **kwargs,
    )


TRANSCRIBE_PROMPT_TEMPLATE = """
당신은 한국 의료문서 판독 전문가입니다. 첨부된 이미지는 한국 병원에서 발급한 {doc_label}입니다.

이미지 안에 보이는 **모든 텍스트를 하나도 빠짐없이, 보이는 그대로** 옮겨 적으세요.
- 인쇄된 글자뿐 아니라 손글씨, 도장/직인 안의 글자, 체크박스 표시(☑, ○, 밑줄 등)도 최대한
  판독해서 포함하세요.
- 글자가 흐릿하거나 확신이 없는 부분은 추측해서 채우지 말고 "[판독불가: 근처 텍스트 맥락]"
  형식으로 표시하세요.
- 표 형태의 내용은 행/열 구조를 유지하며 옮겨 적으세요 (예: "약품명 | 1회투약량 | 1일투여횟수 | 총투약일수").
- 도장이 텍스트 위에 겹쳐 있어 일부 글자가 가려진 경우, 가려지지 않은 부분만이라도 정확히 옮기고
  가려진 부분은 [가려짐]으로 표시하세요.
- 서식의 안내문구(예: "※ 환자의 요구가 있을 때에는...")도 그대로 포함하세요.

번역하거나 요약하지 말고, 원문 한글/숫자를 있는 그대로 정확히 옮겨 적는 것에만 집중하세요.
설명이나 코멘트 없이 옮겨 적은 내용만 출력하세요.
"""


def call_gemini_transcribe(image_bytes: bytes, mime_type: str, api_key: str, doc_label: str) -> str:
    """1단계: 이미지의 모든 텍스트를 원문 그대로 정확히 전사(轉寫).
    구조화를 먼저 시도하는 것보다, 먼저 원문을 정확히 읽어내는 데 집중하게 하면
    손글씨/도장/작은 글씨의 오독이 크게 줄어든다."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            TRANSCRIBE_PROMPT_TEMPLATE.format(doc_label=doc_label),
        ],
        config=_high_thinking_config(),
    )
    return response.text.strip()


def call_gemini_structure(
    image_bytes: bytes, mime_type: str, transcript: str, api_key: str, schema_prompt: str
) -> dict:
    """2단계: 1단계에서 얻은 원문 전사 + 원본 이미지를 함께 제공하여 구조화.
    전사 텍스트와 이미지를 서로 대조하며 채우도록 하여 단일 이미지만 볼 때보다
    필드 누락/오독을 줄인다. 확신이 낮은 필드는 low_confidence_fields 배열에
    필드명을 나열하도록 요청한다."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    combined_prompt = (
        schema_prompt
        + "\n\n---\n"
        + "참고용으로, 같은 이미지를 1차로 옮겨 적은 원문 전사본을 아래에 제공합니다. "
        + "이미지와 전사본을 서로 대조하여 가장 정확한 값을 채우세요. "
        + "전사본에 [판독불가] 또는 [가려짐]으로 표시된 부분은 이미지를 다시 확인해서 "
        + "가능하면 채우고, 그래도 불확실하면 빈 문자열로 두세요.\n\n"
        + "[1차 전사본]\n"
        + transcript
        + "\n\n---\n"
        + "위 스키마의 모든 필드에 더해, 확신이 낮은(추측이 섞인) 필드가 있다면 "
        + '"low_confidence_fields" 키에 해당 필드명들을 배열로 추가하세요 '
        + '(예: "low_confidence_fields": ["doctor_license_no", "diagnosis_korean"]). '
        + "확신이 높으면 빈 배열로 두세요."
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            combined_prompt,
        ],
        config=_high_thinking_config(response_mime_type="application/json"),
    )
    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        raw_text = raw_text.replace("json", "", 1).strip()
    return json.loads(raw_text)


def call_gemini_verify(image_bytes: bytes, mime_type: str, data: dict, api_key: str, doc_label: str) -> dict:
    """3단계: 구조화된 결과를 원본 이미지와 다시 한 번 대조 검증하여, 있을 수 있는
    오류를 스스로 교정하도록 함. 최종 확신이 낮은 필드는 low_confidence_fields에
    유지되어 UI에서 강조 표시된다."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = (
        f"첨부된 이미지는 한국 {doc_label} 원본이고, 아래는 이 이미지에서 AI가 1차로 "
        "추출한 JSON 데이터입니다. 이미지를 다시 꼼꼼히 확인하여 잘못 읽힌 값이 있으면 "
        "수정하세요. 특히 숫자(날짜, 면허번호, 투약일수 등)와 약품명/병명처럼 오류가 "
        "치명적인 항목을 중점적으로 재확인하세요.\n\n"
        "수정한 최종 JSON을 동일한 스키마로 반환하세요 (필드 추가/삭제 없이 값만 수정). "
        "여전히 확신이 낮은 필드는 low_confidence_fields 배열에 유지하세요. "
        "설명 없이 JSON만 출력하세요.\n\n"
        f"[1차 추출 결과]\n{json.dumps(data, ensure_ascii=False)}"
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=_high_thinking_config(response_mime_type="application/json"),
    )
    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        raw_text = raw_text.replace("json", "", 1).strip()
    try:
        return json.loads(raw_text)
    except Exception:
        # 검증 단계 파싱 실패 시, 1차 결과를 그대로 사용 (검증 실패가 전체 흐름을 막지 않도록)
        return data


def call_gemini_vision_extract_pipeline(
    image_bytes: bytes, mime_type: str, api_key: str, doc_label: str, schema_prompt: str,
    progress_callback=None,
) -> dict:
    """전사 → 구조화 → 재검증 3단계 파이프라인. 단일 호출 방식보다 API 호출이 늘어나
    시간이 조금 더 걸리지만, 손글씨·도장·작은 글씨 판독 정확도와 필드 정확도가
    유의미하게 개선된다."""
    if progress_callback:
        progress_callback("1/3 원문 전사 중...")
    transcript = call_gemini_transcribe(image_bytes, mime_type, api_key, doc_label)

    if progress_callback:
        progress_callback("2/3 항목 구조화 중...")
    structured = call_gemini_structure(image_bytes, mime_type, transcript, api_key, schema_prompt)

    if progress_callback:
        progress_callback("3/3 재검증 중...")
    verified = call_gemini_verify(image_bytes, mime_type, structured, api_key, doc_label)

    verified["_raw_transcript"] = transcript
    return verified


def call_gemini_translate_diagnosis(korean_diagnosis: str, api_key: str) -> str:
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = (
        "당신은 의무기록 번역 전문가입니다. 다음은 한국 진단서에 기재된 병명입니다. "
        "이 병명을 국제적으로 통용되는 ICD-10 표준 영문 진단명으로 정확히 번역하세요.\n\n"
        "규칙:\n"
        "- 반드시 실제 존재하는 ICD-10 표준 명칭을 사용하세요. 모르는 단어를 추측해서 "
        "만들어내지 마세요.\n"
        "- 급성/만성, 좌/우, 초발/재발 등 병명의 수식어를 빠뜨리지 말고 영문에 반영하세요.\n"
        "- 확신이 없으면 가장 근접한 상위 분류명을 쓰고 끝에 '(approximate)'를 붙이세요.\n\n"
        "다른 설명 없이 영문 병명만 출력하세요.\n\n"
        f"병명: {korean_diagnosis}"
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL, contents=prompt, config=_high_thinking_config()
    )
    return response.text.strip()


def call_gemini_translate_free_text(korean_text: str, api_key: str) -> str:
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = (
        "다음은 한국 진단서에 기재된 치료 경과 및 향후 소견 내용입니다. "
        "해외 제출용 영문 진단서에 들어갈 수 있도록, 의무기록에 쓰이는 "
        "격식있고 간결한 영문으로 번역하세요. 의학 용어는 정확한 영문 의학 용어를 "
        "사용하고, 원문에 없는 내용을 임의로 추가하지 마세요. "
        "번역문만 출력하고 다른 설명은 하지 마세요.\n\n"
        f"{korean_text}"
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL, contents=prompt, config=_high_thinking_config()
    )
    return response.text.strip()


def call_gemini_translate_medications(medications: list, api_key: str) -> list:
    """처방 약품 목록을 한 번에 영문으로 번역. 특히 성분명(제네릭명/INN)을 우선
    표기하도록 요청. (상품명만 표기하면 해외에서 동일 성분 확인이 어렵기 때문)
    로컬 약품 매핑 테이블(drug_mapping.csv)에 있는 약품은 이 함수 호출 전에
    이미 채워지므로, 여기서는 매핑 테이블에 없는 약품만 전달하는 것을 권장한다."""
    from google import genai

    if not medications:
        return []

    client = genai.Client(api_key=api_key)
    prompt = (
        "당신은 의약품 정보 전문가입니다. 다음은 한국 처방전에 기재된 약품 목록입니다"
        "(JSON 배열). 각 약품에 대해 아래 형식으로 영문 정보를 채워서 JSON 배열로만 "
        "응답하세요. 설명이나 코드블록 없이 순수 JSON만 출력하세요.\n\n"
        "규칙:\n"
        "- 약품명은 '영문 성분명(제네릭명, INN 기준) (상품명이 확인되면 상품명 병기)' "
        "형식으로 작성하세요.\n"
        "- 실제 존재하는 성분명만 사용하고, 확실하지 않으면 성분명을 지어내지 말고 "
        "상품명 로마자 표기와 함께 '(ingredient unconfirmed)'를 표시하세요.\n"
        "- 복합제(여러 성분이 섞인 약)는 모든 성분을 '+'로 나열하세요.\n"
        "- 흔히 오인되는 유사 성분명(예: 유사한 이름의 다른 약)과 혼동하지 않도록 "
        "신중하게 판단하세요.\n\n"
        "입력:\n"
        f"{json.dumps(medications, ensure_ascii=False)}\n\n"
        "출력 형식 (배열 길이와 순서는 입력과 동일하게 유지):\n"
        "[\n"
        "  {\n"
        '    "drug_name_english": "영문 성분명 (상품명)",\n'
        '    "dosage_frequency_english": "예: 1 tablet, 3 times a day",\n'
        '    "duration_english": "예: 7 days",\n'
        '    "substitution_allowed_english": "예: Allowed / Not Allowed (원문에 표시 없으면 빈 문자열)",\n'
        '    "usage_timing_english": "예: After meals / Before meals / Before bedtime (빈 문자열 가능)",\n'
        '    "instructions_english": "예: Take as needed for pain",\n'
        '    "confidence": "high 또는 low (성분명 확신도)"\n'
        "  }\n"
        "]"
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=_high_thinking_config(),
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
                "substitution_allowed_english": "",
                "usage_timing_english": "",
                "instructions_english": "",
                "confidence": "low",
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


PRESCRIPTION_FORM_CSS = """
<style>
    @page { size: A4; margin: 1.3cm; }
    body { font-family: 'NanumGothic'; font-size: 9pt; color: #0d1b4c; }
    .form-wrapper { border: 2.5px solid #0d1b4c; }
    .form-title {
        text-align: center;
        font-family: 'NanumGothic-Bold';
        font-size: 22pt;
        letter-spacing: 14px;
        padding: 10px 0 2px 0;
    }
    .form-subtitle {
        text-align: center;
        font-size: 8pt;
        color: #555555;
        margin-bottom: 6px;
    }
    table.form-table {
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
    }
    table.form-table td, table.form-table th {
        border: 1px solid #0d1b4c;
        padding: 4px 6px;
        font-size: 8.5pt;
        vertical-align: middle;
    }
    td.f-label {
        font-family: 'NanumGothic-Bold';
        background-color: #eef1fa;
        text-align: center;
    }
    .section-title-bar {
        background-color: #0d1b4c;
        color: #ffffff;
        font-family: 'NanumGothic-Bold';
        padding: 4px 8px;
        font-size: 9.5pt;
    }
    .note-text {
        font-size: 7.5pt;
        color: #555555;
        padding: 3px 8px;
    }
    .med-table th {
        background-color: #eef1fa;
        font-family: 'NanumGothic-Bold';
        font-size: 7.8pt;
        text-align: center;
        border: 1px solid #0d1b4c;
        padding: 4px 3px;
    }
    .med-table td {
        font-size: 7.8pt;
        text-align: center;
        border: 1px solid #0d1b4c;
        padding: 4px 3px;
    }
    .med-table td.left { text-align: left; }
    .footer-note {
        font-size: 8pt;
        padding: 8px;
    }
    .blank-cell { height: 20px; }
    .notice-heading {
        font-family: 'NanumGothic-Bold';
        font-size: 9pt;
        margin-top: 14px;
    }
    .notice-body {
        font-size: 8pt;
        font-style: italic;
        color: #444444;
    }
</style>
"""


def _insurance_line(insurance_type_korean: str) -> str:
    categories = [
        ("의료보험", "① Medical Insurance"),
        ("의료보호", "② Medical Aid"),
        ("산재보험", "③ Industrial Accident Insurance"),
        ("자동차보험", "④ Auto Insurance"),
        ("기타", "⑤ Other"),
    ]
    parts = []
    matched_any = False
    for kor, eng in categories:
        if insurance_type_korean and kor in insurance_type_korean:
            parts.append(f"<b>[{eng}]</b>")
            matched_any = True
        else:
            parts.append(eng)
    line = " &nbsp; ".join(parts)
    if not matched_any and insurance_type_korean:
        line += f" &nbsp; (原文/Original: {insurance_type_korean})"
    return line


def generate_prescription_html(data: dict) -> str:
    med_rows = ""
    for med in data.get("medications", []):
        med_rows += f"""
        <tr>
            <td class="left">{med.get('drug_name_english', '') or '-'}</td>
            <td class="left">{med.get('drug_name_korean', '') or '-'}</td>
            <td>{med.get('dosage_frequency_english', '') or '-'}</td>
            <td>{med.get('duration_english', '') or '-'}</td>
            <td>{med.get('substitution_allowed_english', '') or '-'}</td>
            <td>{med.get('usage_timing_english', '') or '-'}</td>
            <td class="left">{med.get('instructions_english', '') or '-'}</td>
        </tr>
        """
    if not med_rows:
        med_rows = '<tr><td colspan="7">No medication information extracted.</td></tr>'

    insurance_line = _insurance_line(data.get("insurance_type_korean", ""))
    validity = data.get("validity_days", "")
    validity_text = (
        f"Valid for {validity} day(s) from the date of issue."
        if validity
        else "Valid for ___ day(s) from the date of issue."
    )

    html = f"""
    <html><head>{PRESCRIPTION_FORM_CSS}</head><body>
    <div class="form-wrapper">
        <div class="form-title">PRESCRIPTION</div>
        <div class="form-subtitle">처방전 (Republic of Korea Standard Prescription Form)</div>

        <table class="form-table">
            <tr>
                <td colspan="3">{insurance_line}</td>
                <td class="f-label" style="width:16%;">Institution Code</td>
                <td style="width:20%;">{data.get('institution_code', '') or '-'}</td>
            </tr>
        </table>

        <table class="form-table">
            <tr>
                <td class="f-label" style="width:18%;">Patient Name</td>
                <td style="width:32%;">{data.get('patient_name_eng') or data.get('patient_name_kor', '') or '-'}</td>
                <td class="f-label" style="width:18%;">Date of Birth</td>
                <td style="width:32%;">{data.get('patient_birth_date', '') or '-'}</td>
            </tr>
            <tr>
                <td class="f-label">Medical Institution</td>
                <td>{data.get('hospital_name', '') or '-'}</td>
                <td class="f-label">Phone</td>
                <td>{data.get('hospital_phone', '') or '-'}</td>
            </tr>
            <tr>
                <td class="f-label">Fax</td>
                <td>{data.get('hospital_fax', '') or '-'}</td>
                <td class="f-label">E-mail</td>
                <td>{data.get('hospital_email', '') or '-'}</td>
            </tr>
        </table>

        <table class="form-table">
            <tr>
                <td class="f-label" style="width:20%;">Diagnosis Code</td>
                <td style="width:20%;">{data.get('diagnosis_classification_code', '') or '-'}</td>
                <td class="f-label" style="width:22%;">Prescribing Physician<br/>(Signature/Seal)</td>
                <td style="width:20%;">{data.get('doctor_name', '')}<br/>______________</td>
                <td class="f-label" style="width:9%;">License Type</td>
                <td style="width:9%;">{data.get('license_type', '') or '-'}</td>
            </tr>
            <tr>
                <td class="f-label">License No.</td>
                <td colspan="5">{data.get('doctor_license_no', '') or '-'}</td>
            </tr>
        </table>
        <div class="note-text">* Diagnosis code is omitted if requested by the patient. (환자의 요구가 있을 때에는 질병분류기호를 기재하지 아니합니다.)</div>

        <div class="section-title-bar">Prescribed Medications (처방 의약품)</div>
        <table class="form-table med-table">
            <tr>
                <th style="width:20%;">Drug Name<br/>(Generic / Brand)</th>
                <th style="width:14%;">Original Name<br/>(Korean)</th>
                <th style="width:12%;">Dose &amp;<br/>Frequency</th>
                <th style="width:9%;">Total<br/>Days</th>
                <th style="width:11%;">Substitution<br/>Allowed</th>
                <th style="width:11%;">Usage<br/>Timing</th>
                <th style="width:23%;">Notes</th>
            </tr>
            {med_rows}
        </table>

        <table class="form-table">
            <tr>
                <td class="f-label" style="width:18%;">Injectable<br/>Medication</td>
                <td style="width:32%;">{data.get('injection_details_korean', '') or '-'}</td>
                <td class="f-label" style="width:18%;">Notes for<br/>Dispensing</td>
                <td style="width:32%;">{data.get('dispensing_notes_korean', '') or '-'}</td>
            </tr>
        </table>

        <table class="form-table">
            <tr>
                <td class="f-label" style="width:20%;">Validity Period</td>
                <td>{validity_text} &nbsp;
                    <span style="font-size:7.5pt; color:#555;">*Must be submitted to the pharmacy within the validity period.</span>
                </td>
            </tr>
        </table>

        <div class="section-title-bar">Medication Dispensing Record (의약품 조제내역) — For Pharmacy Use</div>
        <table class="form-table">
            <tr>
                <td class="f-label" style="width:20%;">Dispensing Institution</td>
                <td style="width:30%;" class="blank-cell"></td>
                <td class="f-label" style="width:20%;" rowspan="3">Changes / Substitutions<br/>to Prescription</td>
                <td style="width:30%;" rowspan="3" class="blank-cell"></td>
            </tr>
            <tr>
                <td class="f-label">Pharmacist Name<br/>(Signature/Seal)</td>
                <td class="blank-cell"></td>
            </tr>
            <tr>
                <td class="f-label">Quantity Dispensed /<br/>Dispensing Date</td>
                <td class="blank-cell"></td>
            </tr>
        </table>

        <div class="footer-note">
            <div class="notice-heading">Notice</div>
            <p class="notice-body">{DISCLAIMER_TEXT}</p>
        </div>
    </div>
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
    drug_mapping_df = load_drug_mapping_table()

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

        if st.button("🔍 AI로 항목 추출하기 (정밀 모드: 전사→구조화→재검증 3단계)", type="primary", disabled=not api_key):
            progress_placeholder = st.empty()

            def _update_progress(msg):
                progress_placeholder.info(f"⏳ {msg}")

            try:
                image_bytes = uploaded_file.getvalue()
                mime_type = uploaded_file.type or "image/jpeg"

                if doc_type_key == "certificate":
                    extracted = call_gemini_vision_extract_pipeline(
                        image_bytes, mime_type, api_key, "진단서", CERTIFICATE_EXTRACTION_PROMPT,
                        progress_callback=_update_progress,
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
                    extracted = call_gemini_vision_extract_pipeline(
                        image_bytes, mime_type, api_key, "처방전", PRESCRIPTION_EXTRACTION_PROMPT,
                        progress_callback=_update_progress,
                    )
                    meds = extracted.get("medications", [])

                    # 로컬 약품 매핑 테이블에서 먼저 조회 (있으면 AI 번역보다 우선 신뢰)
                    unmatched_meds = []
                    unmatched_indices = []
                    for i, med in enumerate(meds):
                        lookup = lookup_english_drug(med.get("drug_name_korean", ""), drug_mapping_df)
                        if lookup.get("matched"):
                            med["drug_name_english"] = lookup["english_name"]
                            med["drug_match_source"] = "mapping_table"
                        else:
                            unmatched_meds.append(med)
                            unmatched_indices.append(i)

                    if unmatched_meds:
                        _update_progress(f"매핑 테이블에 없는 약품 {len(unmatched_meds)}건 AI 번역 중...")
                        translations = call_gemini_translate_medications(unmatched_meds, api_key)
                        for idx, t in zip(unmatched_indices, translations):
                            meds[idx]["drug_name_english"] = t.get("drug_name_english", "")
                            meds[idx]["dosage_frequency_english"] = t.get("dosage_frequency_english", "")
                            meds[idx]["duration_english"] = t.get("duration_english", "")
                            meds[idx]["substitution_allowed_english"] = t.get("substitution_allowed_english", "")
                            meds[idx]["usage_timing_english"] = t.get("usage_timing_english", "")
                            meds[idx]["instructions_english"] = t.get("instructions_english", "")
                            meds[idx]["drug_match_source"] = "AI_translation"
                            meds[idx]["drug_confidence"] = t.get("confidence", "low")

                    # 매핑 테이블로 채워진 약품은 용법/기간 등 나머지 필드를 AI로 보강
                    matched_meds = [meds[i] for i in range(len(meds)) if i not in unmatched_indices]
                    if matched_meds:
                        matched_translations = call_gemini_translate_medications(matched_meds, api_key)
                        mi = 0
                        for i in range(len(meds)):
                            if i not in unmatched_indices:
                                t = matched_translations[mi] if mi < len(matched_translations) else {}
                                meds[i]["dosage_frequency_english"] = t.get("dosage_frequency_english", "")
                                meds[i]["duration_english"] = t.get("duration_english", "")
                                meds[i]["substitution_allowed_english"] = t.get("substitution_allowed_english", "")
                                meds[i]["usage_timing_english"] = t.get("usage_timing_english", "")
                                meds[i]["instructions_english"] = t.get("instructions_english", "")
                                mi += 1

                    extracted["medications"] = meds

                progress_placeholder.empty()
                st.session_state.extracted = extracted
                st.session_state.doc_type = doc_type_key
                st.session_state.purpose = purpose_key
                st.success("추출 완료! 아래에서 내용을 확인 및 수정해주세요.")
            except Exception as e:
                progress_placeholder.empty()
                st.error(f"추출 중 오류가 발생했습니다: {e}")

    if st.session_state.extracted and st.session_state.doc_type == doc_type_key:
        data = st.session_state.extracted
        st.divider()
        st.subheader("3단계: 추출 결과 검수 (필수)")

        low_conf = data.get("low_confidence_fields", [])
        if low_conf:
            st.warning(
                "⚠️ AI가 스스로 확신이 낮다고 표시한 항목입니다. 아래에서 원본 이미지와 "
                f"대조하여 특히 주의 깊게 확인해주세요: **{', '.join(low_conf)}**"
            )

        if data.get("_raw_transcript"):
            with st.expander("🔍 AI가 1차로 옮겨 적은 원문 전사본 보기 (원본과 대조용)"):
                st.text(data["_raw_transcript"])

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
            st.markdown("**의료기관 정보**")
            col1, col2 = st.columns(2)
            with col1:
                data["hospital_name"] = st.text_input("의료기관 명칭", data.get("hospital_name", ""))
                data["institution_code"] = st.text_input("요양기관기호", data.get("institution_code", ""))
                data["hospital_phone"] = st.text_input("전화번호", data.get("hospital_phone", ""))
            with col2:
                data["hospital_fax"] = st.text_input("팩스번호 (선택)", data.get("hospital_fax", ""))
                data["hospital_email"] = st.text_input("e-mail (선택)", data.get("hospital_email", ""))
                data["insurance_type_korean"] = st.text_input(
                    "보험 종류 (의료보험/의료보호/산재보험/자동차보험/기타)", data.get("insurance_type_korean", "")
                )

            st.markdown("**환자 정보**")
            col3, col4 = st.columns(2)
            with col3:
                data["patient_name_eng"] = st.text_input(
                    "환자 성명 영문",
                    data.get("patient_name_eng") or data.get("patient_name_kor", ""),
                )
                data["patient_birth_date"] = st.text_input("생년월일 (YYYY-MM-DD)", data.get("patient_birth_date", ""))
            with col4:
                data["patient_gender"] = st.selectbox(
                    "성별", ["", "M", "F"],
                    index=["", "M", "F"].index(data.get("patient_gender", "")) if data.get("patient_gender") in ["", "M", "F"] else 0,
                )
                data["diagnosis_classification_code"] = st.text_input(
                    "질병분류기호 (선택, 환자 요청시 미기재 가능)", data.get("diagnosis_classification_code", "")
                )

            st.markdown("**처방의료인 정보**")
            col5, col6 = st.columns(2)
            with col5:
                data["doctor_name"] = st.text_input("처방의료인 성명", data.get("doctor_name", ""))
                data["license_type"] = st.text_input("면허종별 (의사/치과의사/한의사)", data.get("license_type", ""))
            with col6:
                data["doctor_license_no"] = st.text_input("면허번호", data.get("doctor_license_no", ""))
                data["validity_days"] = st.text_input("사용기간 (교부일로부터 며칠, 숫자만)", data.get("validity_days", ""))

            col7, col8 = st.columns(2)
            with col7:
                data["prescription_date"] = st.text_input("교부일자 (YYYY-MM-DD)", data.get("prescription_date", ""))
            with col8:
                data["issue_date"] = st.text_input("발급일 (YYYY-MM-DD)", data.get("issue_date", str(date.today())))

            data["injection_details_korean"] = st.text_input(
                "주사제 처방내역 (있는 경우)", data.get("injection_details_korean", "")
            )
            data["dispensing_notes_korean"] = st.text_input(
                "조제시 참고사항 (있는 경우)", data.get("dispensing_notes_korean", "")
            )

            st.markdown("**약품 목록 (확인 및 수정 필요 — 특히 성분명 영문 표기)**")
            st.caption("⚠️ 약품 영문명은 AI 번역 결과입니다. 반드시 의사/약사가 정확한 성분명(INN)인지 확인해주세요.")

            meds = data.get("medications", [])
            for i, med in enumerate(meds):
                source = med.get("drug_match_source", "")
                if source == "mapping_table":
                    badge = "✅ 병원 매핑 테이블에서 확인된 약품입니다."
                elif med.get("drug_confidence") == "low":
                    badge = "⚠️ AI 번역 결과이며 성분명 확신도가 낮습니다. 반드시 확인해주세요."
                elif source == "AI_translation":
                    badge = "ℹ️ AI가 번역한 약품명입니다."
                else:
                    badge = ""

                with st.expander(f"약품 {i+1}: {med.get('drug_name_korean', '(이름 미확인)')}", expanded=True):
                    if badge:
                        st.caption(badge)
                    med["drug_name_korean"] = st.text_input("원문 약품명 (한글)", med.get("drug_name_korean", ""), key=f"med_kor_{i}")
                    med["drug_name_english"] = st.text_input("영문 성분명/상품명", med.get("drug_name_english", ""), key=f"med_eng_{i}")
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        med["dosage_frequency_english"] = st.text_input("용법·용량 (영문)", med.get("dosage_frequency_english", ""), key=f"med_freq_{i}")
                    with c2:
                        med["duration_english"] = st.text_input("투약 기간 (영문)", med.get("duration_english", ""), key=f"med_dur_{i}")
                    with c3:
                        med["usage_timing_english"] = st.text_input("복용 시점 (영문, 식전/식후 등)", med.get("usage_timing_english", ""), key=f"med_timing_{i}")
                    c4, c5 = st.columns(2)
                    with c4:
                        med["substitution_allowed_english"] = st.text_input("대체조제 가능여부 (영문)", med.get("substitution_allowed_english", ""), key=f"med_sub_{i}")
                    with c5:
                        med["instructions_english"] = st.text_input("기타 복용 지침 (영문)", med.get("instructions_english", ""), key=f"med_inst_{i}")
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
