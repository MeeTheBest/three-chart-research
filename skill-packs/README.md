# Versioned astrology Skill packs

`v1/` is the immutable source snapshot used by the website analysis router.
The model never receives a hand-written replacement for these files. Each
analysis stage loads the original root `SKILL.md` plus the original references
routed for that stage; Vedic Core sections are extracted verbatim by Markdown
heading so the whole Core is not pushed into one model request.

The bundled runtime environments are deliberately excluded. They are generated
dependencies, not Skill source. Scripts, reference documents, licences and the
Vedic Codex compatibility modules are retained.

| Skill | Snapshot digest (SHA-256 over sorted file hashes) |
|---|---|
| `ziwei-doushu` | `4bbef3ed0ad17cd45ad0cd568c30d858d1ebd811598f8ef805e58f1c496656cf` |
| `ziping-bazi-analysis` | `b61e31b0613f9a923e93e5ca49f36eb68aca8a87c9f83a1ec659150f1c9024bb` |
| `vedic-astrology` | `760e80a667eb93f7756c505b317ba67bd39e50c8b21a0cd1e53a2b306ed235a4` |

