-- RUHA Manager initial schema (generated; do not edit - add a new migration instead)

CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	entity_type VARCHAR(30) NOT NULL, 
	entity_id INTEGER, 
	action VARCHAR(60) NOT NULL, 
	actor VARCHAR(80) NOT NULL, 
	detail_json TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_audit_entity ON audit_logs (entity_type, entity_id);

CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

CREATE INDEX ix_audit_logs_entity_type ON audit_logs (entity_type);

CREATE TABLE brand_settings (
	"key" VARCHAR(60) NOT NULL, 
	value_json TEXT NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY ("key")
);

CREATE TABLE content_tags (
	id INTEGER NOT NULL, 
	entity_type VARCHAR(20) NOT NULL, 
	entity_id INTEGER NOT NULL, 
	tag VARCHAR(60) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (entity_type, entity_id, tag)
);

CREATE INDEX ix_content_tags_tag ON content_tags (tag);

CREATE INDEX ix_tags_entity ON content_tags (entity_type, entity_id);

CREATE TABLE model_settings (
	"key" VARCHAR(80) NOT NULL, 
	category VARCHAR(30) NOT NULL, 
	value_json TEXT NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY ("key")
);

CREATE INDEX ix_model_settings_category ON model_settings (category);

CREATE TABLE projects (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	description TEXT NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	content_type VARCHAR(40) NOT NULL, 
	workspace_dir VARCHAR(500) NOT NULL, 
	thumbnail_path VARCHAR(500), 
	owner VARCHAR(80) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	archived_at DATETIME, 
	status_before_archive VARCHAR(30), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_projects_content_type ON projects (content_type);

CREATE INDEX ix_projects_created_at ON projects (created_at);

CREATE INDEX ix_projects_status ON projects (status);

CREATE TABLE templates (
	id INTEGER NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	config_json TEXT NOT NULL, 
	file_path VARCHAR(600), 
	is_default BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_templates_kind ON templates (kind);

CREATE TABLE processing_jobs (
	id INTEGER NOT NULL, 
	uid VARCHAR(36) NOT NULL, 
	project_id INTEGER, 
	job_type VARCHAR(40) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	progress FLOAT NOT NULL, 
	step VARCHAR(200) NOT NULL, 
	params_json TEXT, 
	result_json TEXT, 
	error TEXT, 
	attempts INTEGER NOT NULL, 
	depends_on_id INTEGER, 
	created_at DATETIME NOT NULL, 
	started_at DATETIME, 
	completed_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (uid), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(depends_on_id) REFERENCES processing_jobs (id) ON DELETE SET NULL
);

CREATE INDEX ix_processing_jobs_created_at ON processing_jobs (created_at);

CREATE INDEX ix_processing_jobs_job_type ON processing_jobs (job_type);

CREATE INDEX ix_processing_jobs_project_id ON processing_jobs (project_id);

CREATE INDEX ix_processing_jobs_status ON processing_jobs (status);

CREATE TABLE source_videos (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	original_name VARCHAR(300) NOT NULL, 
	path VARCHAR(600) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	duration FLOAT NOT NULL, 
	width INTEGER NOT NULL, 
	height INTEGER NOT NULL, 
	fps FLOAT NOT NULL, 
	video_codec VARCHAR(40), 
	audio_tracks INTEGER NOT NULL, 
	audio_codec VARCHAR(40), 
	thumbnail_path VARCHAR(600), 
	is_current BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX ix_source_videos_project_id ON source_videos (project_id);

CREATE INDEX ix_source_videos_sha256 ON source_videos (sha256);

CREATE TABLE generated_assets (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	video_id INTEGER, 
	kind VARCHAR(30) NOT NULL, 
	slot VARCHAR(40), 
	title VARCHAR(300) NOT NULL, 
	description TEXT NOT NULL, 
	caption TEXT NOT NULL, 
	hashtags_json TEXT, 
	file_path VARCHAR(600), 
	preview_path VARCHAR(600), 
	mime VARCHAR(80), 
	size_bytes INTEGER NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	version INTEGER NOT NULL, 
	metadata_json TEXT, 
	job_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(video_id) REFERENCES source_videos (id) ON DELETE SET NULL, 
	FOREIGN KEY(job_id) REFERENCES processing_jobs (id) ON DELETE SET NULL
);

CREATE INDEX ix_assets_project_kind ON generated_assets (project_id, kind);

CREATE INDEX ix_generated_assets_created_at ON generated_assets (created_at);

CREATE INDEX ix_generated_assets_kind ON generated_assets (kind);

CREATE INDEX ix_generated_assets_project_id ON generated_assets (project_id);

CREATE INDEX ix_generated_assets_status ON generated_assets (status);

CREATE TABLE job_logs (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	level VARCHAR(10) NOT NULL, 
	message TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES processing_jobs (id) ON DELETE CASCADE
);

CREATE INDEX ix_job_logs_job_id ON job_logs (job_id);

CREATE TABLE transcripts (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	video_id INTEGER, 
	language VARCHAR(20), 
	model VARCHAR(60), 
	full_text TEXT NOT NULL, 
	summary TEXT, 
	themes_json TEXT, 
	quotes_json TEXT, 
	plan_json TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(video_id) REFERENCES source_videos (id) ON DELETE SET NULL
);

CREATE INDEX ix_transcripts_project_id ON transcripts (project_id);

CREATE TABLE approvals (
	id INTEGER NOT NULL, 
	asset_id INTEGER NOT NULL, 
	asset_version INTEGER NOT NULL, 
	action VARCHAR(20) NOT NULL, 
	actor VARCHAR(80) NOT NULL, 
	reason TEXT NOT NULL, 
	comment TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_approvals_asset_id ON approvals (asset_id);

CREATE INDEX ix_approvals_created_at ON approvals (created_at);

CREATE TABLE asset_versions (
	id INTEGER NOT NULL, 
	asset_id INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	description TEXT NOT NULL, 
	caption TEXT NOT NULL, 
	hashtags_json TEXT, 
	file_path VARCHAR(600), 
	metadata_json TEXT, 
	status VARCHAR(30) NOT NULL, 
	reason VARCHAR(200) NOT NULL, 
	created_by VARCHAR(80) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (asset_id, version), 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_asset_versions_asset_id ON asset_versions (asset_id);

CREATE TABLE content_calendar (
	id INTEGER NOT NULL, 
	asset_id INTEGER, 
	project_id INTEGER, 
	title VARCHAR(300) NOT NULL, 
	scheduled_for DATETIME NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	campaign VARCHAR(100) NOT NULL, 
	category VARCHAR(60) NOT NULL, 
	platform VARCHAR(40) NOT NULL, 
	notes TEXT NOT NULL, 
	reminder_at DATETIME, 
	reminder_done BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE SET NULL, 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL
);

CREATE INDEX ix_content_calendar_asset_id ON content_calendar (asset_id);

CREATE INDEX ix_content_calendar_scheduled_for ON content_calendar (scheduled_for);

CREATE INDEX ix_content_calendar_status ON content_calendar (status);

CREATE TABLE feedback (
	id INTEGER NOT NULL, 
	project_id INTEGER, 
	asset_id INTEGER, 
	kind VARCHAR(20) NOT NULL, 
	note TEXT NOT NULL, 
	snapshot TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_feedback_asset_id ON feedback (asset_id);

CREATE INDEX ix_feedback_project_id ON feedback (project_id);

CREATE TABLE memory_references (
	id INTEGER NOT NULL, 
	vector_id VARCHAR(80) NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	project_id INTEGER, 
	asset_id INTEGER, 
	text TEXT NOT NULL, 
	metadata_json TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (vector_id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_memory_references_asset_id ON memory_references (asset_id);

CREATE INDEX ix_memory_references_kind ON memory_references (kind);

CREATE INDEX ix_memory_references_project_id ON memory_references (project_id);

CREATE TABLE posters (
	id INTEGER NOT NULL, 
	asset_id INTEGER NOT NULL, 
	headline TEXT NOT NULL, 
	subheadline TEXT NOT NULL, 
	quote TEXT NOT NULL, 
	theme TEXT NOT NULL, 
	template_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (asset_id), 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE CASCADE, 
	FOREIGN KEY(template_id) REFERENCES templates (id) ON DELETE SET NULL
);

CREATE TABLE reels (
	id INTEGER NOT NULL, 
	asset_id INTEGER NOT NULL, 
	category VARCHAR(10) NOT NULL, 
	start_s FLOAT NOT NULL, 
	end_s FLOAT NOT NULL, 
	hook TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	transcript_excerpt TEXT NOT NULL, 
	subtitle_asset_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (asset_id), 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE CASCADE, 
	FOREIGN KEY(subtitle_asset_id) REFERENCES generated_assets (id) ON DELETE SET NULL
);

CREATE TABLE social_posts (
	id INTEGER NOT NULL, 
	asset_id INTEGER NOT NULL, 
	headline TEXT NOT NULL, 
	body TEXT NOT NULL, 
	reference TEXT NOT NULL, 
	template_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (asset_id), 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE CASCADE, 
	FOREIGN KEY(template_id) REFERENCES templates (id) ON DELETE SET NULL
);

CREATE TABLE subtitles (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	asset_id INTEGER, 
	language VARCHAR(40) NOT NULL, 
	format VARCHAR(10) NOT NULL, 
	path VARCHAR(600) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE CASCADE
);

CREATE INDEX ix_subtitles_asset_id ON subtitles (asset_id);

CREATE INDEX ix_subtitles_project_id ON subtitles (project_id);

CREATE TABLE transcript_segments (
	id INTEGER NOT NULL, 
	transcript_id INTEGER NOT NULL, 
	idx INTEGER NOT NULL, 
	start FLOAT NOT NULL, 
	"end" FLOAT NOT NULL, 
	text TEXT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(transcript_id) REFERENCES transcripts (id) ON DELETE CASCADE
);

CREATE INDEX ix_transcript_segments_transcript_id ON transcript_segments (transcript_id);

CREATE TABLE translations (
	id INTEGER NOT NULL, 
	project_id INTEGER NOT NULL, 
	video_id INTEGER, 
	source_language VARCHAR(40), 
	target_language VARCHAR(40) NOT NULL, 
	dubbed BOOLEAN NOT NULL, 
	voice VARCHAR(20), 
	status VARCHAR(20) NOT NULL, 
	srt_asset_id INTEGER, 
	video_asset_id INTEGER, 
	audio_asset_id INTEGER, 
	job_id INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(video_id) REFERENCES source_videos (id) ON DELETE SET NULL, 
	FOREIGN KEY(srt_asset_id) REFERENCES generated_assets (id) ON DELETE SET NULL, 
	FOREIGN KEY(video_asset_id) REFERENCES generated_assets (id) ON DELETE SET NULL, 
	FOREIGN KEY(audio_asset_id) REFERENCES generated_assets (id) ON DELETE SET NULL, 
	FOREIGN KEY(job_id) REFERENCES processing_jobs (id) ON DELETE SET NULL
);

CREATE INDEX ix_translations_project_id ON translations (project_id);
