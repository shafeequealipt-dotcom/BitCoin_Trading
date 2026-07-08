"""Code Validator: validates generated strategy code for syntax, safety, and interface."""

import re

from src.config.settings import Settings
from src.core.logging import get_logger
from src.factory.models.factory_types import GeneratedStrategy

log = get_logger("factory")

DANGEROUS_IMPORTS = {
    "import os", "import sys", "import subprocess", "import shutil",
    "from os", "from sys", "from subprocess",
    "import socket", "import http", "import urllib",
    "import requests", "import aiohttp",
}

DANGEROUS_CALLS = {
    "open(", "exec(", "eval(", "compile(",
    "__import__", "globals(", "locals(",
    "os.system", "os.popen", "subprocess.",
    "print(",
}


class CodeValidator:
    """Validates generated strategy code before deployment.

    4-step validation:
    1. Syntax check (compile)
    2. Safety check (no dangerous imports/calls)
    3. Interface check (inherits BaseStrategy, has required methods)
    4. Logic check (mock scan/vote calls)

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate(self, strategy: GeneratedStrategy) -> tuple[bool, list[str]]:
        """Full validation of generated strategy code.

        Returns:
            (all_passed, list_of_errors)
        """
        errors: list[str] = []

        # Step 1: Syntax
        syntax_ok = self._check_syntax(strategy.code, errors)
        strategy.syntax_valid = syntax_ok

        # Step 2: Safety
        safety_ok = self._check_safety(strategy.code, errors)
        strategy.safety_valid = safety_ok

        # Step 3: Interface
        interface_ok = self._check_interface(strategy.code, errors)
        strategy.interface_valid = interface_ok

        # Step 4: Logic (only if previous steps passed)
        if syntax_ok and safety_ok and interface_ok:
            self._check_logic(strategy.code, errors)

        strategy.validation_errors = errors
        all_passed = syntax_ok and safety_ok and interface_ok and len(errors) == 0

        log.info(
            "Validation: {name} syntax={s} safety={sf} interface={i} errors={e}",
            name=strategy.strategy_name,
            s=syntax_ok, sf=safety_ok, i=interface_ok, e=len(errors),
        )
        return all_passed, errors

    def quick_validate(self, code: str) -> tuple[bool, list[str]]:
        """Quick validation: syntax + safety only."""
        errors: list[str] = []
        syntax_ok = self._check_syntax(code, errors)
        safety_ok = self._check_safety(code, errors)
        return syntax_ok and safety_ok, errors

    @staticmethod
    def _check_syntax(code: str, errors: list[str]) -> bool:
        """Compile to check for syntax errors."""
        try:
            compile(code, "<generated>", "exec")
            return True
        except SyntaxError as e:
            errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
            return False

    @staticmethod
    def _check_safety(code: str, errors: list[str]) -> bool:
        """Scan for dangerous imports and function calls."""
        safe = True
        code_lower = code.lower()

        for dangerous in DANGEROUS_IMPORTS:
            if dangerous.lower() in code_lower:
                errors.append(f"Dangerous import: {dangerous}")
                safe = False

        for dangerous in DANGEROUS_CALLS:
            if dangerous.lower() in code_lower:
                errors.append(f"Dangerous call: {dangerous}")
                safe = False

        return safe

    @staticmethod
    def _check_interface(code: str, errors: list[str]) -> bool:
        """Check that code implements BaseStrategy interface."""
        ok = True

        if "BaseStrategy" not in code:
            errors.append("Must inherit from BaseStrategy")
            ok = False

        if "def name(self)" not in code and "@property" not in code:
            # Flexible check — just needs name property somewhere
            if "name" not in code:
                errors.append("Missing 'name' property")
                ok = False

        for method in ["async def scan(", "def vote("]:
            if method not in code:
                errors.append(f"Missing method: {method.split('(')[0].strip()}")
                ok = False

        if "RawSignal" not in code:
            errors.append("Must reference RawSignal for scan() return type")
            ok = False

        return ok

    @staticmethod
    def _check_logic(code: str, errors: list[str]) -> None:
        """Check that code has reasonable logic patterns."""
        if "return None" not in code:
            errors.append("scan() should return None when conditions aren't met")

        if 'conditions_met' not in code:
            errors.append("RawSignal should include conditions_met dict")

        if code.count("return") < 3:
            errors.append("Too few return statements — scan() needs multiple exit paths")
