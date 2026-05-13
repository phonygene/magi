# Production Safety: Prompt Injection Defense & Content Filtering for Enterprise AI
**Research Stage 5 | Generated: 2026-03-30**

---

## [OBJECTIVE]
Identify evidence-backed defenses against prompt injection and content filtering strategies suitable for enterprise AI assistant deployments. Cover the threat taxonomy, production-grade tools, rate limiting, audit logging, and cost-of-safety trade-offs.

---

## [DATA]
- **Primary sources**: OWASP LLM Top 10 v2025 (official), tldrsec/prompt-injection-defenses GitHub compilation, TrueFoundry guardrail benchmark (n=400 per task), Lakera Guard documentation, Rebuff/Protect AI technical documentation, arXiv papers (2024–2025)
- **Benchmark dataset**: 400 balanced samples per detection task, ~50/50 harmful/safe split
- **Guardrail providers evaluated**: Azure PII, OpenAI Moderation, Azure Content Safety, PromptFoo, Pangea

---

## Section 1: Prompt Injection Taxonomy (OWASP LLM01:2025)

### [FINDING 1] OWASP classifies prompt injection as the #1 LLM vulnerability in 2025, with three canonical attack types.
[STAT:n] Prompt injection appears in >73% of production AI deployments assessed in OWASP security audits
[STAT:effect_size] Ranked LLM01 — the most critical of 10 categories

**Direct Injection**: User input directly overrides or manipulates system prompt behavior (e.g., "Ignore all previous instructions and..."). Attacker is the end user.

**Indirect Injection**: Instructions embedded in external data (documents, web pages, RAG chunks) that the LLM processes. Attacker plants content in the environment, not the prompt. Example: a malicious webpage summarized by an AI agent contains hidden directives.

**Jailbreaking** (distinct from injection per OWASP): Targets the model's safety/alignment mechanisms specifically, rather than functional behavior. Techniques include role-play framing ("act as DAN"), token smuggling, and encoded payloads.

**Multimodal Injection (2025 addition)**: Instructions hidden in images, audio, or structured non-text data processed alongside benign content.

[EVIDENCE] OWASP LLM Top 10 v2025: https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf

---

## Section 2: Defense Patterns for Production

### [FINDING 2] No single defense eliminates prompt injection; production security requires layered defenses.
[STAT:effect_size] Consensus across OWASP, Simon Willison (tldrsec), and 2025 arXiv papers: "Assume this issue isn't fixed now and won't be fixed for the foreseeable future"
[STAT:p_value] Researchers achieved 100% evasion success against Azure Prompt Shield and Meta Prompt Guard in adversarial red-team conditions (2025 study)

### Defense Pattern Catalog

#### 2a. Input Sanitization / Prompt Armoring
- Pattern-matching and semantic classifiers screen prompts before they reach the LLM
- Regex for known injection signatures ("ignore previous instructions", encoding patterns)
- Length limits and character restrictions reduce attack surface
- **Production cost**: Tier 0 (regex): <1ms; Tier 1 (classifier): 20–60ms

#### 2b. Sandwich Defense (System Prompt Wrapping)
- System instructions placed both before and after user content
- Example: `[SYSTEM: rules] [USER: input] [SYSTEM: remember the rules above]`
- [STAT:effect_size] Attack success rate drops from 87% (no defense) to ~65% — moderate improvement only
- **Limitation**: Diluted by long context windows; modern LLMs can lose attention on repeated instructions

#### 2c. Spotlighting (Microsoft Technique)
- Marks untrusted content with consistent transformations (base64, delimiters, XML tags) so the model can distinguish trusted instructions from user/external data
- [STAT:effect_size] Microsoft research: reduces attack success rate from >50% to below 2%
[EVIDENCE] tldrsec/prompt-injection-defenses: https://github.com/tldrsec/prompt-injection-defenses

#### 2d. Canary Tokens / Trip Wires
- Unique secret tokens are embedded in the system prompt
- If the model outputs the canary in its response, an injection has occurred
- Rebuff uses canary tokens to: (a) detect leakage, (b) store attack embeddings in VectorDB for future blocking
- **Limitation**: Deficiencies in canary word checks undermine detection in Vigil and Rebuff implementations

#### 2e. Dual-LLM Pattern (Privileged + Quarantined)
- **Privileged LLM**: receives only trusted input; has access to tools and APIs
- **Quarantined LLM**: processes all external/untrusted content; zero tool access
- Communication between layers uses structured tokens, not free text
- [STAT:effect_size] LLM Self Defense (dual-LLM validation): reduces attack success rate to "virtually 0" on GPT-3.5 and Llama 2

#### 2f. SmoothLLM (Randomized Perturbation)
- Multiple copies of the input are randomly perturbed; predictions are aggregated
- [STAT:effect_size] Reduces attack success rate to below 1% with "provable guarantees"
[EVIDENCE] arXiv / tldrsec compilation

#### 2g. Jatmo (Task-Specific Fine-Tuning)
- Fine-tune models on a constrained task to ignore out-of-distribution instructions
- [STAT:effect_size] <0.5% attack success rate vs. 87% on GPT-3.5-Turbo baseline
- **Trade-off**: Only viable for narrow, well-defined tasks; high upfront cost

#### 2h. Taint Tracking
- Monitor untrusted data flow through the system
- High-risk actions (code execution, sensitive APIs) blocked when "taint level" from external content is elevated
- Architecturally similar to OS-level DEP/ASLR in principle

#### 2i. Blast Radius Reduction (Least Privilege)
- LLMs granted minimum required permissions; most sensitive operations gated behind confirmation steps
- Accepted by all sources as the most reliable foundational baseline
- Does not prevent injection but limits damage when injection succeeds

---

## Section 3: Rebuff — Open Source Injection Detection

### [FINDING 3] Rebuff combines four independent detection layers but remains a prototype with known weaknesses.
[STAT:n] Open-source; maintained by Protect AI; available on PyPI
[CONFIDENCE] Medium — self-described prototype; canary check vulnerabilities documented

**Architecture (4 layers)**:
1. **Heuristics**: Fast regex/keyword screening of known injection patterns
2. **LLM Classifier**: Dedicated language model analyzes intent of incoming prompt
3. **Vector DB**: Embedding similarity against historical attack corpus; blocks prompts similar to previous attacks
4. **Canary Tokens**: Injects secret token into prompt; monitors response for leakage; adds attack to vector store on detection

**Effectiveness comparison** (vs. Vigil):
- Vigil: Optimal when minimal false positive rate is required
- Rebuff: Optimal for average use cases (balanced FP/FN trade-off)
- Both show deficiencies in canary word check implementation

[EVIDENCE] https://github.com/protectai/rebuff | https://blog.langchain.com/rebuff/

---

## Section 4: LLM Guard (Protect AI)

### [FINDING 4] LLM Guard provides a comprehensive scanner suite using fine-tuned BERT models for classification.
[STAT:n] 2.5 million+ downloads; used in production as benchmark tool
[CONFIDENCE] High — well-documented, widely deployed

**Key Scanners**:
- **PromptInjection scanner**: Fine-tuned BERT classifier trained on adversarial examples
- **Anonymize / Deanonymize**: PII detection and redaction (reversible)
- **Secrets detector**: Identifies API keys, passwords in prompts/responses
- **Toxicity / Relevance / Sentiment**: Content quality and safety checks
- **Jailbreak scanner**: Detects safety-bypass attempts

**Performance**:
- Engineered for CPU inference: 5x lower cost vs GPU deployment
- BERT-based classifiers operate in Tier 1 latency range (20–60ms)

**Deployment**: Available as Python library or self-hosted API

[EVIDENCE] https://protectai.com/llm-guard

---

## Section 5: Lakera Guard

### [FINDING 5] Lakera Guard delivers sub-50ms detection across 100+ languages with 98%+ claimed detection rates.
[STAT:effect_size] 98%+ detection rate (vendor claim); sub-50ms latency; 100,000+ new attacks analyzed daily via Gandalf platform
[CONFIDENCE] Medium-High — vendor claim; independent benchmark data limited post-acquisition

**Technical characteristics**:
- Real-time API: single call wraps any LLM request
- Detects: direct injection, indirect injection, jailbreaks, system prompt extraction
- Supports 100+ languages
- Continuously trained on adversarial data from Gandalf (Lakera's public AI security game)

**Enterprise features**:
- SOC2, GDPR, NIST compliant
- Deployment options: SaaS cloud or on-premises
- Custom policies and per-tenant configuration
- Centralized dashboard for real-time oversight and action

**2025 update**: Acquired by Check Point Security; integrated into Infinity Platform and CloudGuard WAF

[EVIDENCE] https://docs.lakera.ai/guard | https://appsecsanta.com/lakera

---

## Section 6: Content Relevance Filtering (Work-Related vs. Off-Topic)

### [FINDING 6] Practical content relevance filtering for enterprise AI uses a tiered classification approach, with LLM-as-judge as the most accurate but costliest method.
[STAT:effect_size] Tiered approach clears >90% of traffic at Tier 0/1 with minimal latency overhead
[CONFIDENCE] Medium — based on Microsoft and industry patterns; no single published benchmark for enterprise topic classification

**Classification Approaches (lowest to highest cost)**:

1. **Blocklist / Allowlist (Tier 0)**: Explicit topic keywords; instant, brittle
2. **Embedding Similarity (Tier 1)**: Compare user query embedding to a "work topic" centroid; fast semantic classification at ~20–50ms
3. **Fine-tuned Topic Classifier (Tier 2)**: Small BERT/DistilBERT trained on work-domain examples vs. off-topic examples; 50–150ms, high accuracy for known domains
4. **LLM-as-Judge (Tier 3)**: Route borderline cases to GPT-4o class model with a relevance scoring prompt; 800–3000ms, highest accuracy, highest cost

**Production pattern**: Apply fast filters to pre-screen 90%+ of traffic. Use LLM-as-judge only for ambiguous cases. This avoids calling expensive models on every interaction.

**Microsoft Azure approach**: Configurable topic filtering in Azure OpenAI; enterprises set strictness level and receive transparency logs.

---

## Section 7: Rate Limiting & Abuse Prevention

### [FINDING 7] Token-based rate limiting is more precise than request-based for AI workloads; per-user quotas with anomaly detection prevent abuse.
[STAT:n] Azure OpenAI, AWS Bedrock, and Google Firebase all implement token-based quotas in production
[CONFIDENCE] High — documented across all major cloud AI providers

**Best practices**:

| Dimension | Recommendation |
|-----------|---------------|
| Limit unit | Tokens (input + output), not requests — captures cost and compute accurately |
| Granularity | Per-user AND per-API-key; tenant-level buckets in multi-tenant systems |
| Tiers | Differentiated limits: free/standard/premium users |
| Reset period | Rolling window (e.g., 100k tokens/day) with grace buffer to avoid cliff drops |
| Headers | Expose `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` to clients |
| Anomaly detection | ML baseline per user; flag sessions with >3 sigma deviation in token consumption |
| Abuse signals | Rapid sequential requests, high-entropy outputs, exfiltration patterns |
| Response on limit | HTTP 429 with `Retry-After` header; never silent drop |

**AI-specific abuse patterns**:
- Prompt harvesting (systematic extraction of system prompt)
- Data exfiltration via indirect injection
- Cost amplification attacks (prompts that force verbose responses)
- Jailbreak farming (automated sweep of bypass variants)

---

## Section 8: Audit Logging for Compliance

### [FINDING 8] EU AI Act Article 12 (enforced August 2026) mandates automatic event logging for high-risk AI systems; SOC2 and GDPR add complementary requirements.
[STAT:n] Three regulatory frameworks converge on similar logging requirements
[CONFIDENCE] High — EU AI Act Regulation 2024/1689 is binding law; SOC2 is industry standard

**Mandatory log fields for LLM interactions**:

```json
{
  "event_id": "uuid-v4",
  "timestamp_utc": "ISO 8601",
  "session_id": "string",
  "user_id": "hashed or pseudonymized",
  "tenant_id": "string",
  "request": {
    "raw_input": "PII-redacted or encrypted",
    "input_token_count": "integer",
    "guardrail_triggered": "boolean",
    "guardrail_category": "injection|jailbreak|pii|off-topic|none",
    "injection_score": "float 0-1"
  },
  "response": {
    "output_token_count": "integer",
    "latency_ms": "integer",
    "model_id": "string",
    "output_hash": "sha256 of response for tamper detection",
    "output_filtered": "boolean",
    "filter_reason": "string"
  },
  "compliance": {
    "pii_detected": "boolean",
    "pii_fields": ["list of field types, not values"],
    "data_residency_region": "string",
    "retention_policy": "30d|90d|7y"
  }
}
```

**Compliance mapping**:
- **EU AI Act Art. 12**: Automatic event logging, traceability of operation throughout lifecycle
- **GDPR**: Pseudonymized user IDs; DPIA records; breach notification within 72h; data subject access/erasure request logs
- **SOC2 Type 2**: Input/output validation records; access review logs (quarterly); change management approvals
- **HIPAA** (if applicable): Unique user ID per access; PHI access audit trail; encryption confirmation

**Retention**: Minimum 90 days operational; 7 years for regulated industries (finance, healthcare)

---

## Section 9: Cost of Safety Layers

### [FINDING 9] Layered safety architecture adds 52–360ms median latency and 5–15% token overhead; fast classifiers handle >90% of traffic at Tier 0/1.
[STAT:ci] 95% CI for content moderation latency: Azure (52ms, CI: [39–65ms]); OpenAI (191ms, CI: varies by load)
[STAT:effect_size] PromptFoo (LLM-judge approach): 1,118ms — 21x slower than regex approach
[STAT:n] n=400 balanced samples per task; TrueFoundry Guardrail Index benchmark, February 2025

**Latency cost by tier**:

| Tier | Method | Latency | Coverage |
|------|--------|---------|----------|
| Tier 0 | Regex / pattern matching | <1ms | ~60% of obvious attacks |
| Tier 1 | BERT classifier (ONNX/TF-Lite) | 20–60ms | ~30% more |
| Tier 2 | API guardrail (Lakera, Azure) | 50–360ms | Borderline cases |
| Tier 3 | LLM-as-judge (GPT-4o) | 800–3000ms | <5% of traffic |

**Token overhead**: Input classification prompts (if using LLM-based classifier) add 50–200 tokens per request; output scanning adds similar overhead.
**Recommended target**: Design system so Tier 3 processes <10% of requests.

**Key insight**: Input validation before the LLM is the cheapest defense — "every token you don't process saves money and latency." Stopping bad inputs pre-inference avoids LLM API costs entirely.

---

## [LIMITATION]
1. **Adversarial robustness gap**: 100% evasion rates against production systems (Azure Prompt Shield, Meta Prompt Guard) were demonstrated in 2025 red-team studies — all defenses are probabilistic, not absolute.
2. **Benchmark generalizability**: TrueFoundry benchmark used 400 samples; real production attack distributions differ. F1 scores may degrade significantly on novel attack patterns.
3. **Vendor claims**: Lakera Guard's 98% detection rate is a vendor claim; no independent third-party audit was found post-Check Point acquisition (March 2025).
4. **Rebuff prototype status**: Self-described as a prototype; canary check deficiencies documented; not recommended as sole defense in high-risk deployments.
5. **Regulatory flux**: EU AI Act high-risk classification criteria are still being finalized for enterprise AI assistants; logging requirements may expand.
6. **Context window evolution**: Sandwich defense and instruction hierarchy defenses degrade as context windows grow (128k–1M tokens); research lags behind model capability.

---

## Summary of Key Recommendations

| Priority | Action | Evidence Basis |
|----------|--------|---------------|
| P0 | Apply least-privilege architecture (blast radius reduction) | Universal consensus, foundational |
| P0 | Log all AI interactions with mandatory fields (OWASP + EU AI Act) | Regulatory mandate Aug 2026 |
| P1 | Implement tiered input validation (regex -> classifier -> API guardrail) | TrueFoundry benchmark; latency data |
| P1 | Use Spotlighting for RAG/tool-using agents (indirect injection) | Microsoft: <2% attack success |
| P1 | Token-based rate limits with per-user anomaly detection | Azure, AWS, GCP production patterns |
| P2 | Integrate Lakera Guard or LLM Guard for production guardrails | 52–360ms overhead; proven deployment |
| P2 | Deploy Dual-LLM pattern for agents with tool access | Near-0% attack success rate |
| P2 | Embed canary tokens in system prompts | Detects leakage; feeds VectorDB learning |
| P3 | Fine-tune topic classifier for enterprise content relevance filtering | Tiered approach; cost-efficient |

---

## Figures
- `figures/guardrail_benchmark.png` — F1 score and latency by guardrail provider
- `figures/defense_latency_tiers.png` — Latency tiers for layered defense architecture
- `figures/rebuff_architecture.png` — Rebuff 4-layer detection pipeline
- `figures/defense_effectiveness.png` — Attack success rates by defense type

## Sources
- OWASP LLM Top 10 v2025: https://genai.owasp.org/llmrisk/llm01-prompt-injection/
- tldrsec/prompt-injection-defenses: https://github.com/tldrsec/prompt-injection-defenses
- TrueFoundry Guardrail Benchmark: https://www.truefoundry.com/blog/benchmarking-llm-guardrail-providers
- Rebuff (Protect AI): https://github.com/protectai/rebuff
- LLM Guard: https://protectai.com/llm-guard
- Lakera Guard: https://docs.lakera.ai/guard
- Introl Production Guide 2025: https://introl.com/blog/llm-security-prompt-injection-defense-production-guide-2025
- Requesty Compliance Checklist: https://www.requesty.ai/blog/security-compliance-checklist-soc-2-hipaa-gdpr-for-llm-gateways-1751655071
- TrueFoundry Rate Limiting: https://www.truefoundry.com/blog/rate-limiting-in-llm-gateway
- Latency-Safe Guardrails: https://medium.com/@ThinkingLoop/latency-safe-guardrails-classifiers-policies-that-dont-slow-llms-283d38411052
