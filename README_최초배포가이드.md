# 영문 진단서 변환 도구 - 처음부터 배포하기 (완전 초보 가이드)

## 최종 폴더 구조 (이렇게 만들어야 함)

```
영문진단서변환도구/              ← 레포 이름 (원하는 대로)
├── streamlit_app.py            ← 홈 화면 (루트에 위치)
├── requirements.txt            ← 루트에 위치
└── pages/                      ← 이 폴더 안에 아래 2개
    ├── english_medical_certificate.py
    └── diagnosis_mapping.csv
```

---

## 1단계: GitHub에 새 레포 만들기

1. github.com 로그인 → 우측 상단 `+` 버튼 → `New repository`
2. Repository name 입력 (예: `medical-cert-translator`)
3. `Public` 또는 `Private` 선택 (아무거나 상관없음, Streamlit Cloud 무료 요금제는 Public 권장)
4. `Add a README file` 체크 (편의상)
5. `Create repository` 클릭

## 2단계: 루트에 파일 2개 업로드

레포 첫 화면(루트)에서:

1. `Add file` → `Upload files`
2. `streamlit_app.py` 와 `requirements.txt` 두 개를 드래그해서 업로드
3. 하단 `Commit changes` 클릭

## 3단계: pages 폴더 만들면서 나머지 2개 업로드

GitHub는 빈 폴더를 따로 만드는 기능이 없고, **파일을 올릴 때 경로를 지정하면 폴더가 자동 생성**됩니다.

1. 다시 `Add file` → `Upload files`
2. `english_medical_certificate.py` 와 `diagnosis_mapping.csv` 두 파일을 드래그
3. 업로드 화면에 나오는 파일명 입력창에서, 파일명 앞에 `pages/`를 붙여줍니다
   - 예: `pages/english_medical_certificate.py`
   - 예: `pages/diagnosis_mapping.csv`
   - (파일을 드래그하면 보통 파일명이 자동으로 채워지는데, 그 앞부분에 `pages/`만 타이핑해서 추가하면 됩니다)
4. `Commit changes` 클릭

업로드 후 레포 메인 화면에 `pages` 폴더가 생기고, 그 안에 파일 2개가 들어가 있으면 성공입니다.

## 4단계: Streamlit Cloud에서 앱 만들기

1. share.streamlit.io (또는 streamlit.io) 로그인
2. `Create app` (또는 `New app`) 클릭
3. `Deploy a public app from GitHub` 선택
4. Repository: 방금 만든 레포 선택
5. Branch: `main`
6. **Main file path: `streamlit_app.py`** ← 이 부분이 중요합니다 (루트에 있는 홈 화면 파일)
7. App URL은 원하는 대로 설정

## 5단계: Gemini API 키 등록 (Secrets)

1. 앱 배포 화면(또는 배포 후 대시보드)에서 `Advanced settings` 또는 앱 설정 → `Secrets`
2. 아래 내용 입력
   ```
   GEMINI_API_KEY = "여기에_실제_발급받은_키_붙여넣기"
   ```
3. 저장

> Gemini API 키가 없으시면 https://aistudio.google.com 에서 로그인 후 `Get API Key`로 무료 발급 가능합니다.

## 6단계: 배포

1. `Deploy!` 클릭
2. 1~3분 정도 빌드 시간이 걸립니다
3. 완료되면 왼쪽 사이드바에 "english medical certificate" 페이지가 보여야 정상입니다

---

## 에러 발생 시 체크리스트

| 증상 | 원인 |
|---|---|
| 사이드바에 페이지가 안 보임 | `pages/` 폴더 경로가 잘못됨 (루트에 그냥 올라갔을 가능성) |
| `ModuleNotFoundError: No module named 'docx'` | `requirements.txt`에 `python-docx` 빠짐 |
| `ModuleNotFoundError: No module named 'google'` | `requirements.txt`에 `google-genai` 빠짐 |
| CSV 관련 오류 (매핑 안 됨) | `diagnosis_mapping.csv`가 `pages/` 폴더 밖에 있음 |
| API 키 오류 | Secrets에 `GEMINI_API_KEY` 미등록 또는 오타 |

막히는 부분 있으면 그 화면 스크린샷 보내주세요, 바로 봐드릴게요.
