# config.py — All configurable values for ScholarEar

# Claude model for summarization
CLAUDE_MODEL = "claude-sonnet-4-6"

# Default voice per TTS engine
TTS_VOICES = {
    "edge": "en-US-JennyNeural",
    "openai": "nova",
    "elevenlabs": "Ember",
}

# Default TTS engine
DEFAULT_TTS_ENGINE = "edge"

# Max summary length guidance (sentences per section)
MAX_SUMMARY_SENTENCES = 4

# Pause duration between sections (seconds, used for SSML/silence insertion)
SECTION_PAUSE_SECONDS = 1.5

# Output directory
OUTPUT_DIR = "audio"

# Section headings to detect (order matters for display)
SECTION_PATTERNS = [
    "Abstract",
    "Introduction",
    "Background",
    "Related Work",
    "Literature Review",
    "Methodology",
    "Methods",
    "Approach",
    "System Design",
    "Experiments",
    "Experimental Setup",
    "Evaluation",
    "Results",
    "Findings",
    "Discussion",
    "Analysis",
    "Conclusion",
    "Summary",
    "Future Work",
]

# Sections to skip during summarization
SKIP_SECTIONS = ["References", "Acknowledgments", "Acknowledgements", "Appendix"]

# Chunking fallback: target characters per chunk when no sections detected
CHUNK_TARGET_CHARS = 3000
