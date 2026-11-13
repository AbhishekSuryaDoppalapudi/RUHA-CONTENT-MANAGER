import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st
from database.db import init_db, list_projects, add_feedback
from app.pipeline import run_pipeline

st.set_page_config(page_title="Local AI Content Studio", page_icon="🎬", layout="wide")
init_db()

st.title("🎬 Local AI Content Studio")
st.caption("Turn one long video into five reels, one highlight poster and two encouragement posts — on your laptop.")

with st.sidebar:
    st.header("Local AI settings")
    whisper_model = st.selectbox("Whisper model", ["small", "base", "medium", "tiny"], index=0,
                                 help="Small is a sensible starting point for 16 GB RAM.")
    ollama_model = st.text_input("Ollama model", "qwen2.5:3b")
    device = st.selectbox("Whisper device", ["auto", "cpu", "cuda"], index=0)
    compute = st.selectbox("Compute type", ["int8", "float16", "int8_float16"], index=0)
    st.caption("Models run locally. Download models once before using offline.")

st.subheader("1. Upload a video")
uploaded = st.file_uploader("Choose a local video", type=["mp4", "mov", "mkv", "avi", "webm"])
if uploaded:
    st.video(uploaded)
    if st.button("✨ Generate content package", type="primary", use_container_width=True):
        upload_dir = ROOT / "storage" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(uploaded.name).name
        saved = upload_dir / safe_name
        saved.write_bytes(uploaded.getbuffer())
        bar = st.progress(0)
        status = st.empty()
        def progress(p, message):
            bar.progress(min(100, int(p)))
            status.info(message)
        try:
            result = run_pipeline(saved, ollama_model=ollama_model,
                                  whisper_model=whisper_model,
                                  whisper_device=device, compute_type=compute,
                                  progress=progress)
            st.success(f"Finished! Project: {result['project_id']}")
            st.write(f"Output folder: `{result['output_dir']}`")
            st.json(result["analysis"])
            st.info("Open the project folder to review the generated MP4, PNG, SRT, transcript and captions.")
        except Exception as e:
            st.error(f"Processing failed: {e}")
            st.exception(e)

st.divider()
st.subheader("2. Previous projects")
projects = list_projects()
if not projects:
    st.caption("No projects yet.")
else:
    for p in projects:
        with st.expander(f"{p['title']} — {p['status']} — {p['created_at'][:19]}"):
            st.write(f"Project ID: `{p['id']}`")
            st.write(f"Output: `{p['output_dir']}`")
            if p["error"]:
                st.error(p["error"])
