-- Word-level timestamps from faster-whisper, [{"w": " grace,", "s": 1.2, "e": 1.6}, ...] per transcript segment.
-- Older transcripts keep NULL here (captions then spread the words of a line evenly over its time).
ALTER TABLE transcript_segments ADD COLUMN words_json TEXT;
