---
tags: [geoproduct-engineer]
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

I'm trying out the GeoProduct toolkit in "D:/sideproject/geoproduct engineer skills" before I point it at real data. Using its offline demo area, Sample Town (bbox 0.0015,0.0015,0.0097,0.0097), make me a dataset of the hospitals and clinics, with each one matched to its building where that's possible. I want a GeoJSON file and a short report.md saying how sure you are about each place and what's uncertain. Run the toolkit from the command line (uv run --directory "D:/sideproject/geoproduct engineer skills" geoproduct --help shows how) and keep its workspace in the folder where you save your outputs.
