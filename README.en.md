 Mini-DB (English)

  A course project for Large-Scale Platform Software Design Practicum: build a simplified database system from scratch,
  step by step.

  Mini-DB spans three courses and implements a complete SQL execution pipeline:

  SQL text
    → [compiler] Token stream → AST → semantic checks → logical plan
    → [engine]   plan → operators (CreateTable/Insert/SeqScan/Filter/Project/Delete)
    → [storage]  page-level I/O (get_page/write_page) + cache (LRU/FIFO) + disk file

  Features

  - ✅ SQL compiler: lexing → parsing → semantic analysis → plan generation (full four-stage visualization)
  - ✅ Page-based storage system: 4KB pages, free-page reuse, LRU/FIFO cache replacement, dirty-page flushing,
    crash-restart recovery
  - ✅ Database execution engine: Volcano-model operators, table heap storage, persistent system catalog, end-to-end
    CRUD
  - ✅ Interactive CLI: compiler mode and database execution mode, with Token/AST/Plan output in multiple formats
  - ✅ Zero third-party dependencies: Python standard library only; tests use unittest

  Tech Stack

  Python 3.11+ (3.13 recommended), no third-party runtime dependencies.

  Why Python:

  - High development efficiency — Token/AST/Plan tree structures are most concise as dataclasses;
  - The stdlib collections.OrderedDict naturally fits the LRU cache (matches the OS-module cache-management
    requirement);
  - struct / bytes make row serialization and page-based storage simulation easy;
  - Easy debugging — the interactive CLI showcases the full Token stream → AST → semantic checks → plan pipeline.

  Directory Structure

  mini-db/
  ├── AGENTS.md                    # Project conventions (module boundaries, dev rules, branching)
  ├── README.md
  ├── .gitignore
  ├── run.bat / run.sh             # One-click run scripts (Windows / Unix)
  ├── test.bat / test.sh           # One-click test scripts
  ├── demo.sql                     # End-to-end demo SQL
  ├── auto_validate.py             # Fully automated brute-force storage tests
  ├── export_sqlite.py             # Export Mini-DB data to a standard SQLite file
  ├── requirements.txt             # Placeholder (no third-party deps for now)
  ├── docs/
  │   ├── architecture.md          # Overall architecture
  │   ├── interface-contract.md    # Inter-module interface contract (single source of truth)
  │   └── task-tiers.md            # Task tier list (P0/P1/P2)
  ├── src/
  │   ├── compiler/                # Module 1: SQL compiler
  │   │   ├── lexer/               #   Lexical analysis
  │   │   ├── parser/              #   Syntax analysis
  │   │   ├── semantic/            #   Semantic analysis + Catalog
  │   │   └── planner/             #   Plan generation
  │   ├── storage/                 # Module 2: page-based storage system
  │   │   ├── page/                #   Page manager
  │   │   ├── cache/               #   Cache & replacement policy
  │   │   ├── disk/                #   Disk file I/O
  │   │   └── storage_manager.py   #   Unified entry point StorageManager
  │   ├── engine/                  # Module 3: database system
  │   │   ├── executor/            #   Execution operators
  │   │   ├── catalog/             #   System catalog (persistent)
  │   │   ├── storage_engine/      #   Storage engine (row/page mapping)
  │   │   ├── expr.py              #   Predicate evaluator
  │   │   └── database.py          #   Database facade (entry point used by the CLI)
  │   └── main.py                  # CLI entry point
  ├── tests/                       # Unit tests + integration regression (test_merge3.py)
  └── data/                        # Runtime data directory (database file mini.db)

  Quick Start

  ▎ Run the following from the project root (the directory containing src/).

  # Compiler mode: print Token stream / AST / semantic checks / plan
  python -m src.main --file tests/compiler/sql/valid.sql

  # Interactive compiler (type SQL, end with ;, type exit to quit)
  python -m src.main

  # Print the plan in a specific format (tree / json / sexpr / all)
  python -m src.main --file demo.sql --plan-format json

  # Database execution mode: actually execute SQL and print result sets
  python -m src.main --execute
  python -m src.main --execute --file demo.sql

  # Print storage [_CACHE] logs (off by default)
  python -m src.main --execute --file demo.sql --verbose

  # Run all tests (-t . must not be omitted)
  python -m unittest discover -s tests -t . -p "test_*.py" -v

  One-click run on Windows (recommended)

  On Windows, python may be the Microsoft Store placeholder (which errors when run directly), and the terminal's default
  GBK encoding garbles Chinese output. The bundled launcher scripts handle both automatically (switch to UTF-8, prefer
  the .venv interpreter):

  :: Run the compiler (arguments are passed through to python -m src.main)
  run.bat --file tests\compiler\sql\valid.sql
  run.bat                            :: interactive mode
  run.bat --execute --file demo.sql  :: database execution mode

  :: Run all tests
  test.bat

  In cmd / PowerShell, double-click or type the script name; in Git Bash use ./run.sh / ./test.sh.

  Create the virtual environment once on first use:

  py -m venv .venv

  (No third-party dependencies, so no pip install is needed; requirements.txt is a placeholder.)

  Supported SQL (P0 scope)

  ┌─────────────┬────────────────────────────────────────────────────┬─────────────────────────────────────────────┐
  │  Statement  │                       Syntax                       │                    Notes                    │
  ├─────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────┤
  │ Create      │ CREATE TABLE t (id INT, name VARCHAR(32), score    │ Types: INT / FLOAT / VARCHAR(n) / TEXT      │
  │ table       │ FLOAT);                                            │                                             │
  ├─────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────┤
  │ Insert      │ INSERT INTO t VALUES (1, 'Alice', 90.5), (2,       │ Multi-row values and explicit column lists  │
  │             │ 'Bob', 80.0);                                      │                                             │
  ├─────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────┤
  │ Query       │ SELECT id, name FROM t WHERE score > 82.0;         │ * / selected columns; single-condition      │
  │             │                                                    │ WHERE                                       │
  ├─────────────┼────────────────────────────────────────────────────┼─────────────────────────────────────────────┤
  │ Delete      │ DELETE FROM t WHERE id = 2;                        │ With or without WHERE (no WHERE = truncate  │
  │             │                                                    │ table)                                      │
  └─────────────┴────────────────────────────────────────────────────┴─────────────────────────────────────────────┘

  Comparison operators: =  <>  !=  <  <=  >  >=

  Scope constraints (per docs/task-tiers.md, only P0 is currently implemented):

  - WHERE supports only a single condition column op value, and does not support AND/OR/NOT, arithmetic expressions, or
    nested parentheses;
  - SELECT is single-table only, no JOIN / subqueries;
  - No query optimization — the plan shape is fixed at Project → Filter → SeqScan;
  - UPDATE, ORDER BY, GROUP BY, EXPLAIN, etc. raise a SyntaxError rather than being silently parsed.

  Architecture Overview

  The three modules are developed independently and in parallel; engine is the single meeting point that understands
  both sides:

  ┌─────────────────────────────────────────────────────────┐
  │  CLI / interaction layer (src/main.py)                   │
  └─────────────────────────────────────────────────────────┘
                      │                      ▲
                      ▼                      │ result sets / errors
  ┌─────────────────────────────────────────────────────────┐
  │  Module 3: engine/ database system                       │
  │  executor  catalog  storage_engine                       │
  └─────────────────────────────────────────────────────────┘
          ▲ logical plan                │ page-level I/O
          │                              ▼
  ┌───────────────┐      ┌──────────────────────────────────┐
  │ Module 1:     │      │ Module 2: storage/ page-based    │
  │ compiler/     │      │ system  cache → page → disk      │
  └───────────────┘      └──────────────────────────────────┘
                                                      │
                                                      ▼
                                              data/mini.db (4KB pages)

  - Module 1 compiler/: a four-stage pipeline (lex → parse → semantic → plan) that only "translates SQL into a plan" and
    is unaware of disk, pages, and cache.
  - Module 2 storage/: stores bytes in 4KB pages, providing get_page / write_page / alloc_page and LRU/FIFO caching;
    unaware of SQL, table, and row semantics.
  - Module 3 engine/: Volcano-model operators that translate a plan into page-level access, including persistent catalog
    and restart recovery.

  See docs/architecture.md for details, and docs/interface-contract.md for inter-module interfaces.

  Tests

  # All unittest cases (compiler / storage / engine unit tests + cross-module integration)
  python -m unittest discover -s tests -t . -p "test_*.py" -v
  test.bat          # Windows
  ./test.sh         # Git Bash / macOS / Linux

  # Fully automated brute-force storage tests (crash-restart / LRU dirty pages / free-page reuse / non-4KB rejection)
  python auto_validate.py

  tests/test_merge3.py is the cross-module integration regression covering gaps the unit tests miss: one-shot four-stage
  compilation, the shared predicate evaluator (7 comparison operators / NULL three-valued logic / INT-FLOAT promotion),
  end-to-end CRUD through the Database facade, data + catalog recovery after reopening multiple tables, compile errors
  propagating as failed results, storage free-list and magic-number checks, and the [_CACHE] verbose flag.

  Tool Scripts

  ┌────────────────────┬───────────────────────────────────────────────────────────────────────────────────────┐
  │       Script       │                                        Purpose                                        │
  ├────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
  │ run.bat / run.sh   │ One-click run (auto-handles UTF-8 encoding and interpreter selection)                 │
  ├────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
  │ test.bat / test.sh │ One-click run of all unittest cases                                                   │
  ├────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
  │ auto_validate.py   │ Fully automated brute-force storage tests; prints only 🎉 ALL TESTS PASSED on success │
  ├────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
  │ export_sqlite.py   │ Exports data/mini.db schema + data to a standard SQLite file for tools like Navicat   │
  └────────────────────┴───────────────────────────────────────────────────────────────────────────────────────┘

  # Export Mini-DB data to SQLite (directly openable in Navicat)
  python export_sqlite.py                    # data/mini.db -> mini_db_export.db
  python export_sqlite.py --out 学生库.db     # custom output filename

  Task Tiers (currently P0 only)

  Full list in docs/task-tiers.md.

  ┌────────────┬─────────────────────────────────────────────────────────────────────────────────────┬─────────────┐
  │    Tier    │                                        Scope                                        │   Status    │
  ├────────────┼─────────────────────────────────────────────────────────────────────────────────────┼─────────────┤
  │ P0         │ Lexer + token positions, four SQL statements, AST, Catalog, semantic checks,        │ ✅ Done     │
  │ Required   │ logical plan, error location, basic tests                                           │             │
  ├────────────┼─────────────────────────────────────────────────────────────────────────────────────┼─────────────┤
  │ P1         │ AND/OR/NOT, complex expressions, ≥2 optimization rules, smart error diagnostics,    │ ⏸ On hold   │
  │ Advanced   │ plan visualization, hidden-test pass rate                                           │             │
  ├────────────┼─────────────────────────────────────────────────────────────────────────────────────┼─────────────┤
  │ P2         │ JOIN/GROUP BY, fuzz testing, optimization framework, cost model, EXPLAIN, advanced  │ 🚫 Out of   │
  │ Extension  │ error recovery                                                                      │ scope       │
  └────────────┴─────────────────────────────────────────────────────────────────────────────────────┴─────────────┘

  Out-of-scope ban: features not on the P0 list must not be implemented; "easy wins" that belong to P1/P2 are only
  recorded in docs/task-tiers.md, to be revisited once P0 is fully green and confirmed.

  Related Docs

  - AGENTS.md — project conventions: module boundaries, global dev rules, branching strategy and collaboration
    constraints
  - docs/architecture.md — overall architecture
  - docs/interface-contract.md — inter-module interface contract (the single cross-module communication agreement)
  - docs/task-tiers.md — task tier list (P0/P1/P2)
