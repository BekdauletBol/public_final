# Diagrams

Editable sources are PlantUML (`*.puml`); exports are in `export/` (PNG and SVG).

| File | Diagram |
|---|---|
| 01-system-context.puml | System context (C4 level 1) |
| 02-use-case.puml | Use case diagram |
| 03-component.puml | Component diagram (C4 level 3, draft) |
| 04-data-model.puml | Entity–relationship diagram (Alembic migration 0001) |
| 05-sequence-stream.puml | Pairing, streaming and event flow (sequence) |

Re-export: `java -jar plantuml.jar -tpng -tsvg -o export *.puml` (PlantUML 1.2025.4, Graphviz required).
