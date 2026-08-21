---
icon: material/code-tags
---

# API Reference

The modules behind the CLI. Most users never import any of this — but
[adding a task](adding_a_task.md) uses `spec` and `uv`, and the module docstrings are where
the reasoning for each design decision lives.

| module | reach for it when |
|---|---|
| [`spec`](#spec) | writing a task: `@task`, `Guard`, `Skip`, `Failed` |
| [`uv`](#uv) | writing a task body: the ways to reach a tool |
| [`config`](#config) | reading a resolved setting, or adding one |
| [`runner`](#runner) | understanding prerequisite order and outcomes |
| [`cli`](#cli) | understanding how the registry becomes commands |

---

## spec

The task model: the registry, the decorator, guards, outcomes, and layer resolution.

::: rhiza_task.spec

---

## uv

The ways rhiza reaches a tool, and nothing else.

::: rhiza_task.uv

---

## config

Configuration, and the resolution order that replaces make's `?=` and `+=`.

::: rhiza_task.config

---

## runner

Prerequisite resolution, guard evaluation and outcome bookkeeping.

::: rhiza_task.runner

---

## cli

The command line, generated from the registry rather than hand-maintained.

::: rhiza_task.cli

---

## Task modules

The gates themselves, each loaded through the `rhiza_task.tasks` entry-point group. Every
module docstring names the make fragment it replaces, and records any behaviour that
changed on purpose.

### Python

::: rhiza_task.tasks.python

### Rust

::: rhiza_task.tasks.rust

### Go

::: rhiza_task.tasks.go

### Quality

::: rhiza_task.tasks.quality

### Testing extras

::: rhiza_task.tasks.extras

### Book and notebooks

::: rhiza_task.tasks.book

### Dev

::: rhiza_task.tasks.doctor

### GitHub helpers

::: rhiza_task.tasks.github

### Docker

::: rhiza_task.tasks.docker

### Git LFS

::: rhiza_task.tasks.lfs

### Paper

::: rhiza_task.tasks.paper

### Presentation

::: rhiza_task.tasks.presentation
