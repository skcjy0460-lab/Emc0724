# 영문 진단서/처방전 변환 도구 - 배포 가이드 (HTML+PDF, 최종본)

## 최종 폴더 구조

```
레포 루트/
├── streamlit_app.py
├── requirements.txt
└── pages/
    ├── english_medical_certificate.py
    ├── diagnosis_mapping.csv
    ├── drug_mapping.csv
    └── fonts/
        ├── NanumGothic-Regular.ttf
        └── NanumGothic-Bold.ttf
```

> ⚠️ `drug_mapping.csv`가 새로 추가되었습니다. `diagnosis_mapping.csv`와 마찬가지로
> `pages` 폴더 안, `english_medical_certificate.py`와 같은 위치에 있어야 합니다.

## 이번 업데이트로 바뀐 점 (품질 개선)

1. **모델 업그레이드**: `gemini-3.6-flash` → `gemini-3.7-flash` + 사고 수준(thinking level) 최대치 적용. 속도보다 정확도를 우선하도록 설정했습니다.
2. **추출 방식을 3단계로 개편**: 기존에는 이미지 1장을 보고 한 번에 구조화된 JSON을 뽑았는데, 이제는
   - 1단계: 이미지의 모든 텍스트를 원문 그대로 옮겨 적기 (손글씨·도장·작은 글씨까지 최대한 판독)
   - 2단계: 원문 전사본 + 이미지를 함께 대조하며 항목별로 구조화
   - 3단계: 구조화된 결과를 이미지와 다시 대조해서 스스로 오류를 교정 (재검증)
   
   이 방식이 이미지 1장만 보고 바로 항목을 뽑는 것보다 오독이 훨씬 적습니다. 다만 API 호출이 늘어나서 처리 시간이 조금 더 걸립니다.
3. **확신도 낮은 항목 표시**: AI가 스스로 "이 항목은 확신이 낮다"고 판단하면 화면에 경고로 표시되고, AI가 1차로 옮겨 적은 원문 전사본도 펼쳐볼 수 있어 원본과 대조하기 쉬워졌습니다.
4. **약품 매핑 테이블 추가** (`drug_mapping.csv`): 병명처럼 약품도 미리 등록해둔 목록이 있으면 AI 번역보다 먼저 그 값을 사용합니다. 지금은 자주 쓰이는 약품 35개 정도만 넣어뒀는데, **이미 만들어두신 '약품 Master DB' 프로젝트 데이터를 이 형식(korean_name, english_name 두 컬럼)으로 변환해서 채워 넣으시면 정확도가 크게 올라갑니다.** AI가 성분명을 추측하는 것보다 실제 등록된 데이터를 조회하는 게 훨씬 정확하기 때문입니다.
5. **병명 매핑 테이블 대폭 확장**: 20개 → 125개 (근골격계, 심혈관, 대사/내분비, 호흡기, 소화기, 비뇨/신장, 신경계, 피부, 이비인후/안과, 감염, 정신건강, 산부인과, 소아, 치과, 종양 등 실제 임상에서 자주 쓰이는 진단명 위주)

## 참고: 처리 시간 안내

3단계 파이프라인 도입으로 항목 추출에 걸리는 시간이 이전보다 늘었습니다 (이미지 1장당 대략 API 호출이 3~4배). 화면에 "1/3 원문 전사 중... → 2/3 항목 구조화 중... → 3/3 재검증 중..." 진행 상황이 표시되니 참고해주세요.

## 업로드 순서 (GitHub 웹 UI)

1. 레포 루트에 `streamlit_app.py`, `requirements.txt` 업로드 (기존에 이미 있다면 `requirements.txt`만 새 내용으로 덮어쓰기)
2. `pages/` 폴더에 `english_medical_certificate.py`, `diagnosis_mapping.csv` 업로드
   - 파일명 앞에 `pages/`를 붙여서 업로드 (예: `pages/english_medical_certificate.py`)
3. `pages/fonts/` 폴더를 만들면서 폰트 2개 업로드
   - `pages/fonts/NanumGothic-Regular.ttf`
   - `pages/fonts/NanumGothic-Bold.ttf`
   - (파일명 앞에 `pages/fonts/`를 붙여서 업로드하면 폴더가 자동 생성됩니다)
4. 커밋

## requirements.txt 내용 (기존 파일 완전히 이 내용으로 교체)

```
streamlit
google-genai
pandas
xhtml2pdf
reportlab
```

> 이번 버전은 Word(.docx) 생성을 뺐기 때문에 `python-docx`는 더 이상 필요 없습니다
> (남아있어도 에러는 안 나지만, 안 쓰는 패키지라 빼는 걸 권장합니다).

## Secrets 설정

기존과 동일하게 `GEMINI_API_KEY` 등록 (이미 등록되어 있다면 그대로 사용)

## 이번 버전에서 바뀐 점

1. **출력 형식**: Word(.docx) → HTML + PDF 동시 생성
   - HTML: 웹에서 바로 열람 가능, 이메일 첨부 등에 활용
   - PDF: 실제 제출용 표준 문서 형식
   - PDF는 한글 폰트(나눔고딕)를 직접 내장해서 만들기 때문에, 어떤 PDF 뷰어에서 열어도 한글이 깨지지 않습니다 (원문 한글이 필요한 항목이 있을 경우 대비)
2. **문서 종류 추가**: 진단서(Medical Certificate) + 처방전(Prescription) 둘 다 지원
   - 처방전은 약품명(성분명 영문 우선 표기), 용법·용량, 투약기간, 복용지침을 표 형태로 정리
   - 처방전의 약품 영문명은 AI 번역 결과이므로, 화면에 "의사/약사 확인 필요" 경고가 항상 표시됩니다

## 사용 순서

1. 문서 종류 선택 (진단서 / 처방전)
2. (진단서인 경우) 용도 선택 (Fit to Fly / 보험 / 일반)
3. 한글 스캔본/사진 업로드 → "AI로 항목 추출하기"
4. 추출 결과 검수 (처방전은 약품별로 펼쳐서 확인)
5. "영문 문서 생성" 클릭 → 화면에서 미리보기 후 HTML/PDF 각각 다운로드
6. 담당 의사(처방전은 약사도) 확인·서명, 직인 날인 후 제출

## 에러 발생 시 체크리스트

| 증상 | 원인 |
|---|---|
| PDF에서 한글이 네모(□)로 깨짐 / 글자가 안 보임 | `fonts` 폴더가 `pages` 안에 없거나 파일명이 다름 |
| `ModuleNotFoundError: No module named 'xhtml2pdf'` 또는 `'reportlab'` | requirements.txt 반영 안 됨, 또는 재배포 안 됨 |
| 병명/약품 매핑 관련 오류 | `diagnosis_mapping.csv` 위치 확인 (pages 폴더 안) |
| API 키 오류 | Secrets에 GEMINI_API_KEY 미등록 |
