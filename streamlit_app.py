# -*- coding: utf-8 -*-
"""
메인 홈 화면
이 파일은 레포의 루트(최상위)에 위치하며, Streamlit Cloud 배포 시
'Main file path'로 지정하는 파일입니다.
"""

import streamlit as st

st.set_page_config(page_title="병원 업무 도구 모음", page_icon="🏥", layout="wide")

st.title("🏥 병원 업무 도구 모음")
st.write(
    "왼쪽 사이드바에서 원하는 도구를 선택하세요.\n\n"
    "- **영문 진단서 변환 도구**: 한글 진단서를 업로드하면 해외 제출용 영문 초안을 생성합니다."
)
