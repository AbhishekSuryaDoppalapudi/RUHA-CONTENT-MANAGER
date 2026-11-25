-- Reel Editor: reels the owner builds from chosen parts of the sermon. New tables only - nothing existing is altered.
-- (The older "reels" table belongs to the automatic planner and is left as it is.)
CREATE TABLE editor_reels (
	id INTEGER NOT NULL,
	project_id INTEGER NOT NULL,
	title VARCHAR(300) NOT NULL DEFAULT '',
	status VARCHAR(20) NOT NULL DEFAULT 'draft',
	version INTEGER NOT NULL DEFAULT 1,
	language VARCHAR(40) NOT NULL DEFAULT 'English',
	aspect VARCHAR(8) NOT NULL DEFAULT '9:16',
	options_json TEXT NOT NULL DEFAULT '{}',
	caption_edits_json TEXT NOT NULL DEFAULT '{}',
	source VARCHAR(60) NOT NULL DEFAULT 'manual',
	cut_key VARCHAR(64),
	preview_path VARCHAR(600),
	asset_id INTEGER,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(asset_id) REFERENCES generated_assets (id) ON DELETE SET NULL
);

CREATE INDEX ix_editor_reels_project ON editor_reels (project_id);

CREATE TABLE reel_segments (
	id INTEGER NOT NULL,
	reel_id INTEGER NOT NULL,
	position INTEGER NOT NULL,
	start FLOAT NOT NULL,
	"end" FLOAT NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(reel_id) REFERENCES editor_reels (id) ON DELETE CASCADE
);

CREATE INDEX ix_reel_segments_reel ON reel_segments (reel_id, position);

CREATE TABLE reel_versions (
	id INTEGER NOT NULL,
	reel_id INTEGER NOT NULL,
	version INTEGER NOT NULL,
	snapshot_json TEXT NOT NULL,
	note VARCHAR(200) NOT NULL DEFAULT '',
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (reel_id, version),
	FOREIGN KEY(reel_id) REFERENCES editor_reels (id) ON DELETE CASCADE
);
