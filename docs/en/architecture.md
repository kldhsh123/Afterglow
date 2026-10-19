# Architecture

```mermaid
flowchart LR
  User["User / external client"] --> API["Afterglow FastAPI<br/>OpenAI-compatible API"]
  Frontend["Frontend<br/>Chat / diagnostics UI"] --> API

  subgraph Afterglow["Afterglow core"]
    API --> Auth["API authentication<br/>Trace ID"]
    Auth --> LayerA["Concurrent Layer A<br/>Retrieval / relationship / life state"]
    LayerA --> Retrieve["HybridRetriever<br/>Five vector recalls + Recent Live<br/>RRF + optional rerank"]
    LayerA --> Life["LifeStateManager<br/>life_state + circadian + LIFE_*"]
    LayerA --> Relationship["Relationship memory<br/>Recent user facts"]
    Auth --> Policy["Turn response policy<br/>Rules + optional model refinement"]
    Auth --> Proactive["Proactive engine<br/>Profile scheduling + disturbance gates"]
    Auth --> Web["Optional web search<br/>URL content fetching"]
    Retrieve --> Policy
    Life --> Policy
    Relationship --> Policy
    Proactive --> Prompt
    Policy --> Prompt["Prompt Builder<br/>persona + memories + state + policy"]
    Web --> Prompt
    Prompt --> ChatLLM["Main chat model<br/>OpenAI-compatible"]
    ChatLLM --> Filter["Output filtering<br/>Placeholders / sticker / life-event"]
    Filter --> API
    Filter --> Writeback["Live Memory writeback<br/>user_new / ai_generated"]
    Filter --> Schedule["Optional schedule extraction<br/>schedule-hint"]
    Filter --> LifePatch["Life event writeback<br/>life-update"]
  end

  subgraph Ingestion["Offline ingestion pipeline"]
    message["Exported chats<br/>JSON / JSONL + media"] --> Plugin["Import plugin<br/>Afterglow / QQ / WeFlow / Douyin"]
    Plugin --> Normalize["NormalizedMessage<br/>Roles / kinds / placeholders"]
    Normalize --> Clean["Clean / emoji placeholders / normalize mentions"]
    Clean --> Redact["PII redaction"]
    Redact --> Split["Session splitting"]
    Split --> Chunk["Three history text chunk types<br/>friend / window / response_pair"]
    Chunk --> Embed["Embedding model"]
    Chunk --> Label["Optional labeling model"]
    Split --> PersonaBuild["persona / style / circadian<br/>Lightweight proactive profile"]
    message --> ImageImport["import-images<br/>References + original images"]
    ImageImport --> Vision["VLM image descriptions"]
    Vision --> ImageEmbed["Description embeddings"]
  end

  subgraph OfflineAnalysis["Explicit offline analysis (separate from import)"]
    message --> RelationshipAnalysis["xuwen analyze<br/>Timeline / personality / Life / experimental"]
    message --> ProactiveAnalysis["xuwen analyze-proactive<br/>Proactive statistics + optional AI attribution"]
    ProactiveAnalysis --> QualityCheck["Read-only quality check<br/>ready / limited / not_ready"]
  end

  subgraph Storage["Local persistence"]
    Lance[(LanceDB<br/>A/B/C history text + D image descriptions<br/>live + relationship)]
    Persona["persona_card.md<br/>persona_style_profile.json"]
    Circadian["circadian_profile.json<br/>Observed circadian profile"]
    LegacyProactive["proactive_profile.json<br/>Import-time proactive profile"]
    AnalysisReports["timeline / personality / life<br/>Explicit relationship reports"]
    ExperimentalReports["experimental/personality_profile.json<br/>experimental/prompt_context.md"]
    ProactiveReport["proactive_analysis.json<br/>Independent proactive report"]
    LifeFile["life_state.json<br/>Today life timeline"]
    ImageCache["Historical image cache<br/>.data/images"]
    Stickers["Sticker cache<br/>.data/stickers/index.json + files"]
  end

  Embed --> Lance
  ImageEmbed --> Lance
  ImageImport --> ImageCache
  Label --> Lance
  PersonaBuild --> Persona
  PersonaBuild --> Circadian
  PersonaBuild --> LegacyProactive
  RelationshipAnalysis --> AnalysisReports
  RelationshipAnalysis --> ExperimentalReports
  ProactiveAnalysis --> ProactiveReport
  Retrieve --> Lance
  Relationship --> Lance
  Prompt --> Persona
  AnalysisReports -. "Personality / Life via separate switches; timeline view only" .-> Prompt
  ExperimentalReports -. "Experimental context requires both switches" .-> Prompt
  ProactiveReport -. "Preferred profile and topic retrieval" .-> Proactive
  LegacyProactive -. "Fallback if report is absent" .-> Proactive
  Life --> Circadian
  Life --> LifeFile
  LifePatch --> LifeFile
  Writeback --> Lance
  Schedule --> API
  API --> ImageCache
  API --> Stickers
```

```mermaid
mindmap
  root((Afterglow))
    Afterglow core
      Import and cleaning
      Import plugins
      Vectors by source
      Image description index
      Hybrid retrieval
      Persona
      Circadian profile
      Life timeline
      Explicit relationship analysis
        Timeline report
        Personality profile
        Long-term Life habits
      Independent proactive analysis
        Optional AI motive attribution
        Read-only data quality check
      Relationship memory
      Response policy
      Web access
      Sticker and image caches
      OpenAI-compatible API
      Diagnostics
    Frontend testing and diagnostics
      Chat UI
      Memory sources
      Settings
      Diagnostic panel
    Models and services
      Main chat model
      Embedding model
      Labeling model
      Life state / web intent model
      Optional response policy model
      Optional cross-encoder reranker
      Tavily or SearXNG
```

## Key design choices

- **Layered hybrid retrieval:** A/B/C historical text, D `history_images` descriptions and runtime `live_messages` form five vector recall paths. HybridRetriever runs these concurrently and reads Recent Live. Outer Layer A runs retrieval, relationship context and Life decisions in parallel.
- **Optional cross-encoder ranking:** `CROSS_RERANK_ENABLED=true` applies a dedicated model (for example `xop3qwen8breranker` or `qwen3-rerank`) after RRF, before the main prompt. Relevance and latency depend on provider/model; do not assume a universal timing guarantee.
- **Optional Query Rewrite:** A small model expands short colloquial queries into up to three retrieval variants. Hits merge by `(best_rank, distance)` to reduce sparse recall.
- **Optional adaptive chunking:** `CHUNKING_STRATEGY=adaptive` uses topic boundaries through heuristics and an optional small model instead of only fixed 12-message windows. Session concurrency is configurable; runtime depends on corpus and model.
- **Multiple reply bubbles:** Double newlines in one assistant response allow the frontend to display multiple bubbles with separate avatars/timestamps and staggered timing. The API still returns one assistant message. Third-party clients implement splitting if wanted.
- **Three weighting layers:** Recency boost (capped at 15%), warmth weighting and source trust distinguish recent/live/history material.
- **Memory source separation:** `user_new` records user facts; `ai_generated` supports continuity at low weight and does not enter persona/style distillation. Long-term imitation evidence comes from `human_original` history.
- **Controllable AI accumulation:** `AI_GENERATED_LONG_TERM_ENABLED=false` restricts AI semantic memory to the same conversation by default. Enabling it allows low-weight cross-conversation retrieval and potential drift.
- **Growing memory:** Turns are asynchronously written to `live_messages`; storage is not a one-time snapshot.
- **Per-turn response policy:** Rules choose serious, comforting, playful, redirecting, silent, image or sticker behavior, and avoid escalating distress. Optional `RESPONSE_POLICY_MODEL_ENABLED` refinement is bounded: it cannot lower risk or undo protected rule decisions.
- **Silence and delayed display:** A no-reply policy bypasses the main model and returns `finish_reason="silenced"`, the sentinel and `policy.should_reply=false`. Clients apply `policy.reply_delay_seconds`; the backend does not block for presentation delays.
- **Three independent offline paths:** `xuwen import` builds vectors and persona/style/circadian/lightweight proactive profiles; `xuwen analyze` explicitly builds timeline/personality/Life/experimental reports; `xuwen analyze-proactive` independently computes proactive statistics and optional AI motive attribution. Ordinary import/analysis does not trigger proactive attribution.
- **Opt-in report consumption:** Timeline is display-only. Personality enters the prompt only with `ANALYSIS_PERSONALITY_PROMPT_ENABLED=true`; Life habits require `ANALYSIS_LIFE_CONTEXT_ENABLED=true`.
- **Experimental proactive learning:** Disabled and not recommended by default. Scheduling prefers independent `proactive_analysis.json`, falls back to import-time `proactive_profile.json`, then stored windows. Quality checks are read-only, with no AI calls or automatic enablement. Polling rechecks disturbance gates and chooses topic hooks from memory, relationship context and recent cache.
- **Real time and Life state:** Each model call receives current time in the configured zone. A small model maintains daily state for questions about activities, food or sleep. Failed calls write a minimal fallback state to avoid repeated update loops.
- **Optional web access:** Tavily or SearXNG adds public current information when explicitly requested; disabled by default.
- **Large-table indexing:** `uv run python -m xuwen.ingestion.cli index --language en` creates LanceDB IVF_PQ indexes. `cli optimize` merges incremental data. Benefits depend on table size and workload.
- **No fine-tuning:** RAG, prompts and persona cards supply context without modifying model weights.
- **Letter-inspired UI:** Warm paper, dark blue ink, serif typography, ambient particles, human-paced typing and memory-source overlays.
