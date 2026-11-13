from app.pipeline import run_pipeline

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Local AI Content Agent")
    parser.add_argument("video", help="Path to a local video file")
    parser.add_argument("--ollama-model", default="qwen2.5:3b")
    parser.add_argument("--whisper-model", default="small")
    args = parser.parse_args()
    result = run_pipeline(args.video, ollama_model=args.ollama_model,
                          whisper_model=args.whisper_model)
    print(result)
