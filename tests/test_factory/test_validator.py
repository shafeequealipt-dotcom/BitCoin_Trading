"""Tests for CodeValidator."""

import pytest

from src.factory.models.factory_types import GeneratedStrategy
from src.factory.validator import CodeValidator


class TestSyntaxCheck:
    def test_valid_syntax(self, factory_settings, valid_strategy_code):
        v = CodeValidator(factory_settings)
        strategy = GeneratedStrategy(
            id="gen_1", pattern_id="pat_1", strategy_name="test", code=valid_strategy_code,
        )
        passed, errors = v.validate(strategy)
        assert strategy.syntax_valid is True

    def test_invalid_syntax(self, factory_settings):
        v = CodeValidator(factory_settings)
        strategy = GeneratedStrategy(
            id="gen_1", pattern_id="pat_1", strategy_name="test",
            code="def broken(:\n  pass",
        )
        passed, errors = v.validate(strategy)
        assert strategy.syntax_valid is False
        assert any("Syntax error" in e for e in errors)


class TestSafetyCheck:
    def test_dangerous_imports_rejected(self, factory_settings, invalid_strategy_code):
        v = CodeValidator(factory_settings)
        strategy = GeneratedStrategy(
            id="gen_1", pattern_id="pat_1", strategy_name="test",
            code=invalid_strategy_code,
        )
        passed, errors = v.validate(strategy)
        assert strategy.safety_valid is False
        assert any("import os" in e.lower() for e in errors)

    def test_print_rejected(self, factory_settings):
        v = CodeValidator(factory_settings)
        code = 'class Foo:\n    def bar(self):\n        print("hello")\n        return None'
        strategy = GeneratedStrategy(
            id="gen_1", pattern_id="pat_1", strategy_name="test", code=code,
        )
        passed, errors = v.validate(strategy)
        assert any("print(" in e for e in errors)

    def test_safe_code_passes(self, factory_settings, valid_strategy_code):
        v = CodeValidator(factory_settings)
        strategy = GeneratedStrategy(
            id="gen_1", pattern_id="pat_1", strategy_name="test", code=valid_strategy_code,
        )
        v.validate(strategy)
        assert strategy.safety_valid is True


class TestInterfaceCheck:
    def test_valid_interface(self, factory_settings, valid_strategy_code):
        v = CodeValidator(factory_settings)
        strategy = GeneratedStrategy(
            id="gen_1", pattern_id="pat_1", strategy_name="test", code=valid_strategy_code,
        )
        v.validate(strategy)
        assert strategy.interface_valid is True

    def test_missing_basestrategy(self, factory_settings):
        v = CodeValidator(factory_settings)
        code = "class Foo:\n    async def scan(self): return None\n    def vote(self): return ('N',0,'')"
        strategy = GeneratedStrategy(
            id="gen_1", pattern_id="pat_1", strategy_name="test", code=code,
        )
        v.validate(strategy)
        assert strategy.interface_valid is False


class TestFullValidation:
    def test_valid_code_passes_all(self, factory_settings, valid_strategy_code):
        v = CodeValidator(factory_settings)
        strategy = GeneratedStrategy(
            id="gen_1", pattern_id="pat_1", strategy_name="test", code=valid_strategy_code,
        )
        passed, errors = v.validate(strategy)
        assert passed is True
        assert len(errors) == 0

    def test_quick_validate(self, factory_settings, valid_strategy_code):
        v = CodeValidator(factory_settings)
        passed, errors = v.quick_validate(valid_strategy_code)
        assert passed is True
