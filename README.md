# Nano Banana Pro Image Generation Skill

[日本語版 README](README.ja.md) | **RexGrey Fork** — multi-channel fallback (AI Studio → Vertex SA → 柏拉图). See [Multi-Channel Fork Changes](#multi-channel-fork-changes).

A Claude Code skill for image generation using the Google Nano Banana Pro (Gemini 3 Pro Image) API. Can also be used as a standalone image generation script.

> **This fork adds three fallback channels** so the skill survives GCP billing suspends, rate limits, and transient outages. Drop-in compatible with upstream — `python generate_image.py "prompt"` still works exactly as before. See [Multi-Channel Fork Changes](#multi-channel-fork-changes) below for the new flags and config options.

## Setup

### Requirements

- **Python 3.10 or later** (required by `google-genai` library)

### 1. Clone the Repository

```bash
cd /path/to/your-projects  # any location you prefer
git clone https://github.com/feedtailor/ccskill-nanobanana.git
cd ccskill-nanobanana
```

### 2. Get API Key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click "Get API key" to obtain your API key
    > **Note**: Nano Banana Pro has no free tier, so billing setup is required

### 3. Configure Environment Variables

Copy `.env.example` to create `.env`:

```bash
cp .env.example .env
```

Edit the `.env` file and set your API key:

```
GEMINI_API_KEY=your-api-key-here
```

### 4. Install Dependencies

```bash
# Create venv with Python 3.10 or later
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

### 5. Set Environment Variable (for use as a skill)

Add to your `.bashrc` or `.zshrc`:

```bash
export CCSKILL_NANOBANANA_DIR="/path/to/ccskill-nanobanana"
```

## Usage

### Run from Command Line

```bash
source venv/bin/activate
python generate_image.py "a cat playing piano"
```

### Options

| Option | Description | Default | Choices |
|--------|-------------|---------|---------|
| `--resolution` | Output resolution | 2K | 1K, 2K, 4K |
| `--aspect` | Aspect ratio | 16:9 | 1:1, 16:9, 9:16, 4:3, etc. |
| `--output` | Output directory | ./generated_images | any path |
| `--reference` | Reference image(s) (up to 14) | none | image file path |

### Examples

```bash
# Basic usage
python generate_image.py "sunset coastline"

# High-resolution wide image
python generate_image.py "mountain landscape" --resolution 4K --aspect 16:9

# Specify output directory
python generate_image.py "logo design" --output ./assets/
```

### Image Editing with Reference Images

Edit or modify existing images by providing reference images:

```bash
# Change background
python generate_image.py "change background to sunset" --reference ./original.png

# Use multiple reference images
python generate_image.py "draw this person in this pose" \
    --reference ./person.png \
    --reference ./pose.png
```

Reference image use cases:
- Partial image editing (background change, color adjustment, etc.)
- Style transfer (apply style from another image)
- Character consistency
- Image compositing

## Use as Claude Code Skill

### Install to Other Projects

Install via symbolic link (recommended):

```bash
# Create .claude/skills directory in target project if it doesn't exist
mkdir -p /path/to/your-project/.claude/skills

# Create symbolic link
ln -s $CCSKILL_NANOBANANA_DIR/.claude/skills/nano-banana-pro \
      /path/to/your-project/.claude/skills/nano-banana-pro
```

Claude Code will automatically use this skill when image generation is needed.

Run `git pull` on this repository to update the skill in all linked projects.

### Skill Language Configuration

By default, the skill uses English (`SKILL.md`). To use the Japanese version:

```bash
cd $CCSKILL_NANOBANANA_DIR/.claude/skills/nano-banana-pro

# Switch to Japanese
mv SKILL.md SKILL.en.md
ln -s SKILL.ja.md SKILL.md

# To switch back to English
rm SKILL.md
mv SKILL.en.md SKILL.md
```

## Testing

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

## Specifications

- **Model**: `gemini-3-pro-image-preview` (Nano Banana Pro)
- **Output format**: Automatically determined from API response (PNG/JPEG/WebP)
- **Filename**: Timestamp format (e.g., `20251130_153045.png`, `20251130_153045.jpg`)
- **Watermark**: Generated images include SynthID

## Multi-Channel Fork Changes

This fork (`RexGrey/ccskill-nanobanana`, branch `enhancement/multi-channel`) adds resilience against single-point failures:

### Why

Upstream depends on a single `GEMINI_API_KEY` hitting AI Studio. If Google suspends the project (billing lapse, region restriction, etc.), every generation fails with:

> `"Your project has been denied access. Please contact support."`

This error is **often temporary** (billing suspend that auto-recovers after payment) but the single-channel design turns it into total outage. This fork adds two fallback channels and a billing-aware error classifier.

### Three Channels

| Channel | Auth | When it wins | Cost |
|---------|------|--------------|------|
| `ai_studio` (default first) | `GEMINI_API_KEY` URL param | Healthy days — cheapest, fastest | low |
| `vertex_sa` | Service Account JSON (OAuth2) | AI Studio suspended — Vertex runs on a separate account system | low |
| `bltcy` | Middleman `sk-xxx` key | Both Google channels down — proxy uses its own account pool | medium |

The chain tries providers in order; falls back on transient errors (429, 5xx, "denied access" billing suspend); aborts on permanent errors (400 bad prompt).

### New CLI flags

```bash
# Override chain priority
python generate_image.py "prompt" --provider vertex_sa,ai_studio,bltcy

# Run a single channel only (no fallback — useful for testing)
python generate_image.py "prompt" --only-provider vertex_sa

# Debug: show credential source + chain + fallback trace
python generate_image.py "prompt" --debug
```

### New credential sources

The fork loads credentials from **three places**, in priority order:

1. **ComfyUI Batchbox `secrets.yaml`** — if you already use [ComfyUI-Custom-Batchbox](https://github.com/RexGrey/ComfyUI-Custom-Batchbox), this skill reads its `secrets.yaml` automatically (default path `~/Documents/ComfyUI/custom_nodes/ComfyUI-Custom-Batchbox/secrets.yaml`; override with `CCSKILL_SECRETS_YAML` env). No duplicate key setup needed.
2. **Skill directory `.env`** — backward compatible with upstream.
3. **Process environment variables** — for CI / container use.

See [.env.example](.env.example) for all supported variables.

### Architecture

```
generate_image.py  (CLI + dispatcher)
    ├── config/loader.py        # secrets.yaml + .env + env vars → Credentials dataclass
    ├── auth/sa_token.py        # Service Account JWT → OAuth2 Bearer token (cached)
    └── providers/
        ├── base.py             # Abstract Provider + error taxonomy (Transient/Permanent)
        ├── ai_studio.py        # REST call to generativelanguage.googleapis.com
        ├── vertex_sa.py        # REST call to aiplatform.googleapis.com with SA token
        └── bltcy.py            # OpenAI-compat call to api.bltcy.ai + URL download
```

All providers return raw `bytes` + MIME type — no dependence on `google-genai` SDK for the new code paths (upstream's SDK usage is retained in `requirements.txt` for compatibility).

### Billing-aware error handling

On `"project has been denied access"`:
- Classified as **transient** (not permanent) — the chain falls back to the next provider
- Final error message hints `"check GCP billing status"` instead of misleading `"account banned"`

### Diagnostic output

```bash
$ python generate_image.py "apple" --debug
[debug] credentials: source=secrets.yaml:/.../secrets.yaml + .env+env ai_studio_keys=4 vertex_sa=inline vertex_project=batchbox bltcy_keys=2
[debug] provider chain: ['ai_studio', 'vertex_sa', 'bltcy']
[chain] trying provider: ai_studio
[chain] ✅ success via ai_studio
[Success] Image saved: ./generated_images/20260420_154350.jpg  (via ai_studio, 487KB)
```

### Upstream relationship

- `main` tracks `feedtailor/ccskill-nanobanana@main` for easy merging
- Multi-channel code lives on `enhancement/multi-channel`
- No upstream file is deleted or structurally changed — additions only. Merging upstream changes should be low-conflict.

## License

MIT
