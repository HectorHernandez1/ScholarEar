# ScholarEar

Turn research papers into podcast-style audio summaries.

ScholarEar extracts text from a PDF research paper, detects sections, summarizes each one using Claude, and converts the narration into an MP3 audio file.

## Installation

```bash
# Create and activate conda environment
conda create -n scholar-ear python=3.11 -y
conda activate scholar-ear

# Install dependencies
pip install -r requirements.txt
```

## Setup

Create a `.env` file in the project root with your API keys:

```
ANTHROPIC_API_KEY=sk-ant-...       # Required — used for summarization
OPENAI_API_KEY=sk-...              # Only needed if using --tts openai
ELEVENLABS_API_KEY=...             # Only needed if using --tts elevenlabs
```

## Usage

```bash
# Default (Edge TTS — free, no API key needed)
python scholar_ear.py ~/papers/attention.pdf

# OpenAI TTS
python scholar_ear.py ~/papers/attention.pdf --tts openai

# ElevenLabs TTS
python scholar_ear.py ~/papers/attention.pdf --tts elevenlabs
```

Output is saved to `audio/<semantic-title>.mp3`.

## Supported TTS Engines

| Engine | API Key Required | Notes |
|--------|-----------------|-------|
| Edge TTS (default) | No | Free, uses Microsoft Edge's online TTS service |
| OpenAI TTS | Yes (`OPENAI_API_KEY`) | High-quality, uses `tts-1` model |
| ElevenLabs | Yes (`ELEVENLABS_API_KEY`) | Premium voices, uses `eleven_multilingual_v2` |

## Config Options

Edit `config.py` to customize:

- **Voice per TTS engine** — default voices for Edge (`en-US-GuyNeural`), OpenAI (`nova`), ElevenLabs (`Adam`)
- **Claude model** — model used for summarization
- **Max summary sentences** — controls summary length per section (default: 4)
- **Section pause duration** — silence between sections in seconds
- **Output directory** — where MP3 files are saved (default: `audio/`)
