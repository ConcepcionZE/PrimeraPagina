"""Versioned policies with an explicit, human-only promotion step and a rollback.

Lifecycle of a policy version:
  proposed -> gates_passed -> paper -> approved_pending_owner -> active
Only `promote(..., approved_by=<name>, confirmation=PROMOTE_PHRASE)` makes a
version active, and it records who approved it. Nothing promotes silently.
`rollback()` reactivates the previous active version.

A policy file holds gate thresholds and the calibrator. It never holds risk
limits: those live in config/risk.yaml and RiskLimits, outside this store.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import fields
from typing import Optional

from .calibration import Calibrator
from .gate import Policy

PROMOTE_PHRASE = "PROMOTE"
FORBIDDEN_KEYS = {"risk_per_trade", "max_daily_loss", "max_open_positions", "max_leverage",
                  "kill_drawdown", "venue_max_leverage"}
STATUSES = ["proposed", "gates_failed", "gates_passed", "paper", "approved_pending_owner", "active", "retired"]


class PolicyStore:
    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.registry_path = os.path.join(directory, "registry.json")
        if not os.path.exists(self.registry_path):
            self._write_registry({"active": None, "previous": None, "history": []})

    def _registry(self) -> dict:
        with open(self.registry_path) as f:
            return json.load(f)

    def _write_registry(self, reg: dict) -> None:
        tmp = self.registry_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(reg, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.registry_path)

    def _path(self, version: str) -> str:
        return os.path.join(self.dir, f"policy_{version}.json")

    def versions(self) -> list[str]:
        return sorted(f[len("policy_"):-5] for f in os.listdir(self.dir)
                      if f.startswith("policy_") and f.endswith(".json"))

    def next_version(self) -> str:
        nums = [int(v[1:]) for v in self.versions() if v[1:].isdigit()]
        return f"v{(max(nums) if nums else 0) + 1}"

    def save(self, policy: Policy, calibrator: Optional[Calibrator] = None, status: str = "proposed",
             notes: str = "", evidence: Optional[dict] = None, parent: Optional[str] = None) -> str:
        d = policy.to_dict()
        if FORBIDDEN_KEYS & set(d):
            raise ValueError("a policy may not carry risk limits")
        doc = {"policy": d, "calibrator": (calibrator or Calibrator()).to_dict(), "status": status,
               "notes": notes, "evidence": evidence or {}, "parent": parent,
               "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        with open(self._path(policy.version), "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        return policy.version

    def load_doc(self, version: str) -> dict:
        with open(self._path(version)) as f:
            return json.load(f)

    def load(self, version: Optional[str] = None) -> tuple[Policy, Calibrator]:
        version = version or self._registry()["active"]
        if version is None:
            raise FileNotFoundError("no active policy; run `reflexbot init-policy`")
        doc = self.load_doc(version)
        if FORBIDDEN_KEYS & set(doc["policy"]):
            raise ValueError("policy file carries risk limits; refusing to load")
        names = {f.name for f in fields(Policy)}
        return Policy(**{k: v for k, v in doc["policy"].items() if k in names}), Calibrator.from_dict(doc.get("calibrator"))

    def set_status(self, version: str, status: str, evidence: Optional[dict] = None) -> None:
        if status not in STATUSES or status == "active":
            raise ValueError("use promote() to activate a policy")
        doc = self.load_doc(version)
        doc["status"] = status
        if evidence:
            doc["evidence"].update(evidence)
        with open(self._path(version), "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")

    def active(self) -> Optional[str]:
        return self._registry()["active"]

    def promote(self, version: str, approved_by: str, confirmation: str, bootstrap: bool = False) -> None:
        """Make `version` the active policy. Human-only: needs a name and the phrase."""
        if confirmation != PROMOTE_PHRASE or not approved_by.strip():
            raise PermissionError("promotion needs an approver name and the confirmation phrase")
        doc = self.load_doc(version)
        if not bootstrap and doc["status"] not in ("gates_passed", "paper", "approved_pending_owner"):
            raise PermissionError(f"policy {version} is '{doc['status']}'; it has not passed the gates")
        reg = self._registry()
        if reg["active"] and reg["active"] != version:
            old = self.load_doc(reg["active"])
            old["status"] = "retired"
            with open(self._path(reg["active"]), "w") as f:
                json.dump(old, f, indent=2)
                f.write("\n")
        reg["previous"], reg["active"] = reg["active"], version
        reg["history"].append({"version": version, "approved_by": approved_by, "action": "promote",
                               "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        doc["status"] = "active"
        with open(self._path(version), "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        self._write_registry(reg)

    def rollback(self, approved_by: str) -> str:
        reg = self._registry()
        if not reg["previous"]:
            raise RuntimeError("no previous policy to roll back to")
        cur, prev = reg["active"], reg["previous"]
        for v, st in ((cur, "retired"), (prev, "active")):
            doc = self.load_doc(v)
            doc["status"] = st
            with open(self._path(v), "w") as f:
                json.dump(doc, f, indent=2)
                f.write("\n")
        reg["active"], reg["previous"] = prev, cur
        reg["history"].append({"version": prev, "approved_by": approved_by, "action": "rollback",
                               "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        self._write_registry(reg)
        return prev
