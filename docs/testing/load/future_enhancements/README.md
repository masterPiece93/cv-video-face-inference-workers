# 🔭 Future Enhancements

This folder collects **design proposals** for features that are not yet
implemented in the load-testing framework. Each document is a self-contained
spec — problem statement, design, implementation sketch, affected files, and
acceptance criteria — so that any engineer can pick it up and implement it
later without needing the original context.

These are **not** implemented today. They describe intended future work.

---

## 📑 Index

| # | Enhancement | Status | Summary |
|---|-------------|--------|---------|
| 1 | [Remote Worker Resource Metrics](./01-remote-worker-resource-metrics.md) | 📝 Proposed | Capture CPU & memory KPIs when load testing a worker hosted **elsewhere** (Cloud Run / GKE / remote VM), where the current in-process sampler cannot see the worker. |

---

## 🧩 How to add a new proposal

1. Create a new numbered file: `NN-short-title.md`.
2. Follow the structure used in existing proposals:
   - **Problem / Motivation**
   - **Current Behaviour** (what exists today + why it falls short)
   - **Proposed Design** (options + recommended approach)
   - **Implementation Sketch** (code, CLI flags, affected files)
   - **Testing & Acceptance Criteria**
   - **Phased Rollout**
3. Add a row to the index table above.

---

## 🏷️ Status legend

| Badge | Meaning |
|-------|---------|
| 📝 Proposed | Documented, not started. |
| 🚧 In Progress | Being implemented. |
| ✅ Done | Implemented — move details into the main docs and link back. |
| ❄️ On Hold | Valid idea, deprioritised. |
</content>
</invoke>
