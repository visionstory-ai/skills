# VisionStory Skills

Agent Skills for the [VisionStory API](https://developers.visionstory.ai) — generate AI talking-avatar
videos, create avatars, clone voices, and manage video tasks from any agent that
supports the [Agent Skills](https://github.com/anthropics/skills) format.

## Skills

| Skill | What it does |
|---|---|
| [`visionstory-api`](visionstory-api/SKILL.md) | Create and manage AI avatar videos through the VisionStory OpenAPI. |

## Install

With the [`skills` CLI](https://www.npmjs.com/package/skills):

```bash
npx skills add visionstory-ai/skills --skill visionstory-api
```

Or copy the skill folder into your agent's skills directory manually:

```bash
git clone https://github.com/visionstory-ai/skills.git
mkdir -p <your-agent>/skills
cp -r skills/visionstory-api <your-agent>/skills/
```

## Configure

The skill reads your API key from the environment — it is never passed on the
command line or pasted into chat:

```bash
export VISIONSTORY_API_KEY="sk-vs-..."
```

Create or manage a key in [API Keys](https://developers.visionstory.ai/api-keys).
Read the [API reference](https://developers.visionstory.ai/reference) for endpoint details.

## What's inside a skill

Each skill is a self-contained folder:

- `SKILL.md` — the instructions the agent reads.
- `scripts/` — a zero-dependency Python CLI (`visionstory_api.py`) plus the shared
  client layer (`visionstory_client.py`); stdlib only, Python 3.10+.
- `agents/` — per-platform interface metadata.

## License

MIT — see [LICENSE](LICENSE).

---

This repository is generated — the skill folders are published from the
VisionStory API's canonical sources. Do not edit files here by hand.
