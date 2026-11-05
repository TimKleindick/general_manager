from inspect import signature

from general_manager import GeneralManager, bulk_create_eligibility


def test_bulk_create_eligibility_accepts_manager_keyword() -> None:
    arguments = {"manager": GeneralManager}

    signature(bulk_create_eligibility).bind(**arguments)
    assert bulk_create_eligibility(**arguments) == bulk_create_eligibility(
        GeneralManager
    )
