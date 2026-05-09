# Code Review Benchmark

[English](README.md) · [Русский](README.ru.md)

Методика и набор скриптов для замера качества code-review у LLM на реальном
diff'е: один и тот же diff прогоняется через N моделей, находки агрегируются,
дедуплицируются, размечаются вердиктом, и считаются per-model метрики
precision / recall / hallucination rate.

Автор: Светлана Мелешкина. Лицензия — [MIT](LICENSE).

## Зачем

Публичные LLM-бенчмарки (SWE-bench, HumanEval и т.п.) меряют *генерацию*
кода, а не *ревью*. У ревью другие failure modes: придуманные баги,
пропущенные настоящие, инфляция severity, нарушение формата ответа. Этот
репо — небольшой принципиальный харнесс, который меряет именно их на
*твоём* diff'е, чтобы выбрать модель под *твою* кодовую базу, а не под
чужой лидерборд.

В методике сознательно оставлены два «человеческих» шага — кластеризация и
вердикт по каждому кластеру. Парсинг и сводку делает код, важные суждения —
человек, опираясь на Claude в чате.

## Pipeline

```
[diff + контекстные файлы]
        ↓
1. code_review_benchmark.py     ← OpenRouter, N моделей параллельно
        ↓
   results.json + results/<model>.md
        ↓
   (опц.) Claude-субагенты      ← Opus / Sonnet / Haiku из Claude Code
        ↓
   results_claude_subagent/<model>.md
        ↓
2. aggregate_findings.py parse  ← парсит .md → findings.json (без LLM)
        ↓
   findings.json
        ↓
3. Claude в текущем чате        ← кластеризация, сохраняет clusters.json
        ↓
   clusters.json
        ↓
4. aggregate_findings.py render ← findings + clusters → worklist.md (без LLM)
        ↓
   worklist.md
        ↓
5. Claude в текущем чате        ← вердикт по каждому кластеру: real/smell/nit/wrong
        ↓
   verdicts.md
        ↓
6. compute_metrics.py           ← метрики per-model + лидерборд (без LLM)
        ↓
   worklist_judged.md + leaderboard.md
```

**Принцип:** Python-скрипты не делают LLM-вызовов. Всё, что требует
рассуждения (кластеризация, суждение), делает Claude в текущем чате — обычно
бесплатно по подписке. OpenRouter тратится только на шаге 1, где гоняются
сторонние модели.

## Установка

```bash
pip install -r requirements.txt
```

```powershell
$env:OPENROUTER_API_KEY = "..."   # PowerShell
```
```bash
export OPENROUTER_API_KEY=...     # bash
```

## Использование

### 1. Прогон бенчмарка через OpenRouter

```bash
python code_review_benchmark.py path/to/some.diff \
  -c path/to/file1.cs \
  -c path/to/file2.cs \
  -o runs/<run-id>/results.json
```

Создаст:
- `results.json` — мета + сырые ответы всех моделей
- `results/<model>.md` — каждое ревью отдельным markdown-файлом

**Список моделей:** редактируй `models.json` (display name → OpenRouter
model id) или укажи другой файл флагом `--models-file PATH`. В репо лежит
дефолтный список; подрежь его под свою OpenRouter-квоту и нужные семейства.

**Промт:** шаблон лежит в `prompts/`. По умолчанию используется
`prompts/review.en.txt`. Альтернатива `prompts/review.ru.txt` показывает,
как локализовать тело промта, оставив английские маркеры полей
(`Findings:`, `Location:` и т.д.). Перебить путь — флагом `--prompt PATH`.
В шаблоне доступны два плейсхолдера: `{diff}` и `{context_block}`.

### 2. (опц.) Прогон Claude-субагентов

Запусти Claude-субагентов из чата (Opus / Sonnet / Haiku) и сохрани их
ревью в `results_claude_subagent/Claude_<Model>.md` тем же форматом
заголовков, который ожидает парсер (`1) [severity: ...] ...` или
`### 1) [...]`).

Этот шаг существует потому, что Claude доступен на OpenRouter не у всех, а
прогон через подписку Claude Code обычно бесплатный.

### 3. Парсинг находок

```bash
python aggregate_findings.py parse \
  --results-dir results \
  --subagent-dir results_claude_subagent \
  -o findings.json
```

### 4. Кластеризация (Claude в чате)

Я (Claude) читаю `findings.json`, группирую находки по сути проблемы,
сохраняю в `clusters.json`:

```json
{
  "clusters": [
    {"id": 1, "topic": "...", "consensus_severity": "major", "members": [<int idx>]}
  ]
}
```

### 5. Рендер worklist'а

```bash
python aggregate_findings.py render \
  --findings findings.json \
  --clusters clusters.json \
  -o worklist.md
```

### 6. Судейство (Claude в чате)

Я (Claude) для КАЖДОГО кластера читаю исходный код в указанном месте и
выношу вердикт `real | smell | nit | wrong` с обоснованием. Результат —
`verdicts.md`:

```
## Cluster 1
- Verdict: real
- Confidence: high
- Reason: <одна строка>

## Cluster 2
...
```

### 7. Метрики

```bash
python compute_metrics.py \
  --verdicts verdicts.md \
  --findings findings.json \
  --clusters clusters.json \
  --results results.json
```

Создаст:
- `worklist_judged.md` — оригинальный worklist с проставленными `[x]` и
  заметками судьи (для удобной верификации человеком, особенно для
  low-confidence кластеров)
- `leaderboard.md` — таблица per-model метрик: precision, recall,
  hallucination rate, $/real

## Категории судейства

- **real** — настоящий баг с production-impact: краш, неверный результат,
  деградация на типичных данных, race, утечка, потеря данных
- **smell** — code health, не упадёт: дублирование, плохие имена,
  missing docs, DRY-нарушения, асимметрия API
- **nit** — чистый стиль: whitespace, micro-opt, idiomatic preferences
- **wrong** — модель ошиблась: проблема не существует / неправильно понят
  код / не применима

Tie-breaker: real/smell → smell, smell/nit → nit, smell/wrong →
перепроверь, иначе smell.

## Format compliance

Не все модели идеально следуют формату ответа. В `aggregate_findings.py`
есть regex `ISSUE_RE`, толерантный к типичным отклонениям (`**bold**`-
декорации, `1.` вместо `1)`, severity в markdown и т.п.) плюс пустой по
умолчанию словарь `FORMAT_NOTES = {}` для аннотаций «модель парсится, но с
оговорками» — твои записи отображаются рядом с моделью в `worklist.md`,
чтобы при просмотре чуть пристальнее смотреть на её находки. Заполняй по
наблюдениям своих прогонов; механика — в
[CONTRIBUTING.md](CONTRIBUTING.md#format-compliance-notes).

Оба парсера (`code_review_benchmark.py` для живых API-ответов и
`aggregate_findings.py` для разбора markdown-ревью) ждут английских
маркеров полей, как в `prompts/review.en.txt`: `Findings:`, `Location:`,
`Why it matters:`, `Evidence:`, `Recommendation:` плюс тег
`[severity: blocker/major/minor/nit]`. Меняешь маркеры — обнови regex'ы.

## Структура репозитория

```
ai-code-review-benchmark/
├── README.md
├── README.ru.md
├── LICENSE
├── requirements.txt
├── code_review_benchmark.py        ← OpenRouter-раннер
├── aggregate_findings.py           ← парсер + рендерер worklist (без LLM)
├── compute_metrics.py              ← метрики + лидерборд (без LLM)
├── models.json                     ← дефолтный список моделей (перебивается --models-file)
├── prompts/
│   ├── review.en.txt               ← дефолтный шаблон промта
│   └── review.ru.txt               ← пример с русским телом и английскими маркерами
└── runs/                           ← в .gitignore — твои локальные прогоны
    └── <run-id>/                   ← один прогон = одна папка
        ├── input.diff              ← сам diff (для воспроизводимости)
        ├── results.json            ← сырой output OpenRouter
        ├── results/                ← per-model .md (OpenRouter)
        ├── results_claude_subagent/← per-model .md (Claude-субагенты)
        ├── findings.json           ← распарсенные находки
        ├── clusters.json           ← кластеры от Claude
        ├── worklist.md             ← worklist для разметки
        ├── verdicts.md             ← вердикты по кластерам
        ├── worklist_judged.md      ← worklist + вердикты merged
        ├── leaderboard.md          ← per-model метрики
        └── run.log                 ← stdout прогона
```

**Конвенция id прогона:** `runs/<short-id>/` — id может быть тикетом
(`PROJ-1234`), фичей (`auth-refactor`) или датой
(`2026-05-09-deepseek-only`). Все артефакты прогона — внутри одной папки,
скриптам передавай `--output runs/<id>/...`.

`runs/` в `.gitignore` по умолчанию — чтобы прогоны на приватном коде не
утекали в публичный репозиторий. Убирай эту строку из `.gitignore` только
если конкретный прогон полностью публичный.

## Файлы

### Скрипты (в репо)

| Файл | Формат / что это | Кто создаёт |
|---|---|---|
| `code_review_benchmark.py` | Python — OpenRouter-раннер; зовёт LLM | этот репо |
| `aggregate_findings.py` | Python — парсер + рендерер worklist'а; LLM не зовёт | этот репо |
| `compute_metrics.py` | Python — метрики + лидерборд; LLM не зовёт | этот репо |
| `models.json` | JSON — `{display_name: openrouter_model_id}`; ключи на `_` считаются комментариями | этот репо (перебивается `--models-file`) |
| `prompts/review.en.txt` · `review.ru.txt` | Текстовый шаблон — плейсхолдеры `{diff}` и `{context_block}` | этот репо (перебивается `--prompt`) |

### Артефакты прогона (внутри `runs/<run-id>/`)

Всё ниже — то, что создаёт пайплайн за один прогон.

| Файл | Схема | Кто создаёт |
|---|---|---|
| `input.diff` | Unified diff (текст) | пользователь (`git diff > input.diff`) |
| `results.json` | JSON — `{ meta: {diff_file, diff_size_chars, context_files, ...}, results: { <model>: {status, content, issues, issues_count, usage: {prompt_tokens, completion_tokens, total_tokens}, elapsed_sec} } }` | `code_review_benchmark.py` |
| `results/<model>.md` | Markdown — блок `Findings:` с пронумерованными пунктами: `N) [severity: blocker\|major\|minor\|nit] summary` и подпунктами `- Location:`, `- Why it matters:`, `- Evidence:`, `- Recommendation:` | `code_review_benchmark.py` |
| `results_claude_subagent/<model>.md` | Markdown — тот же формат | Claude-субагенты (запускаются из чата вручную) |
| `findings.json` | JSON — `{ issues: [{model, severity, summary, location, why_it_matters, evidence, recommendation}] }` | `aggregate_findings.py parse` |
| `clusters.json` | JSON — `{ clusters: [{id, topic, consensus_severity, members: [<int idx в issues[]>]}] }` | Claude в чате (одноразово) |
| `worklist.md` | Markdown — кластеры с `[ ]`-чекбоксами (`real` / `smell` / `nit` / `wrong`), готовые к разметке | `aggregate_findings.py render` |
| `verdicts.md` | Markdown — на каждый кластер: `## Cluster N` и строки `- Verdict:`, `- Confidence:`, `- Reason:` | Claude в чате (судейство с чтением исходников) |
| `worklist_judged.md` | Markdown — `worklist.md` с проставленными `[x]` и заметками судьи | `compute_metrics.py` |
| `leaderboard.md` | Markdown — per-model precision / recall / hallucination rate / $/real | `compute_metrics.py` |
| `cost_estimates.json` | JSON — `{ <model>: {usd, source, kind: "actual"\|"estimated"\|"estimated_anthropic"} }` (опционально, нужен для `$/real`) | пользователь (вручную; из OpenRouter dashboard или публичных тарифов) |
| `run.log` | Plain text — stdout шага 1 | `code_review_benchmark.py` (через redirect) |

## Контрибьюшн

См. [CONTRIBUTING.md](CONTRIBUTING.md) — как добавить модель, поменять промт
или обновить regex'ы парсеров при смене маркеров ответа. (Файл на английском —
общая OSS-конвенция.)
