#!/usr/bin/env python3
"""
Compound Interest Calculator
------------------------------
Calculates the future value of an investment using compound interest,
with optional recurring contributions.

Formula used:
    A = P * (1 + r/n)^(n*t) + PMT * (((1 + r/n)^(n*t) - 1) / (r/n))

Where:
    A    = future value
    P    = principal (initial investment)
    r    = annual interest rate (decimal)
    n    = number of times interest is compounded per year
    t    = number of years
    PMT  = regular contribution per compounding period
"""


def get_positive_float(prompt: str) -> float:
    """Prompt the user until a valid non-negative float is entered."""
    while True:
        try:
            value = float(input(prompt))
            if value < 0:
                print("Please enter a value that is zero or positive.")
                continue
            return value
        except ValueError:
            print("Invalid input. Please enter a numeric value.")


def get_positive_int(prompt: str) -> int:
    """Prompt the user until a valid positive integer is entered."""
    while True:
        try:
            value = int(input(prompt))
            if value <= 0:
                print("Please enter a whole number greater than zero.")
                continue
            return value
        except ValueError:
            print("Invalid input. Please enter a whole number.")


def calculate_compound_interest(principal: float,
                                 annual_rate: float,
                                 years: float,
                                 compounds_per_year: int,
                                 contribution: float = 0.0) -> dict:
    """
    Calculate compound interest with optional periodic contributions.

    Returns a dict containing the future value, total contributions,
    and total interest earned.
    """
    r = annual_rate / 100  # convert percentage to decimal
    n = compounds_per_year
    t = years

    # Future value of the initial principal
    principal_growth = principal * (1 + r / n) ** (n * t)

    # Future value of the periodic contributions (ordinary annuity)
    if r == 0:
        contribution_growth = contribution * n * t
    else:
        contribution_growth = contribution * (((1 + r / n) ** (n * t) - 1) / (r / n))

    future_value = principal_growth + contribution_growth
    total_contributed = principal + (contribution * n * t)
    total_interest = future_value - total_contributed

    return {
        "future_value": future_value,
        "total_contributed": total_contributed,
        "total_interest": total_interest,
    }


def main():
    print("=== Compound Interest Calculator ===\n")

    principal = get_positive_float("Enter the initial principal amount: $")
    annual_rate = get_positive_float("Enter the annual interest rate (%): ")
    years = get_positive_float("Enter the number of years to invest: ")
    compounds_per_year = get_positive_int(
        "Enter compounding frequency per year "
        "(1=annually, 4=quarterly, 12=monthly, 365=daily): "
    )
    contribution = get_positive_float(
        "Enter additional contribution per compounding period (0 if none): $"
    )

    results = calculate_compound_interest(
        principal, annual_rate, years, compounds_per_year, contribution
    )

    print("\n--- Results ---")
    print(f"Future Value:        ${results['future_value']:,.2f}")
    print(f"Total Contributed:   ${results['total_contributed']:,.2f}")
    print(f"Total Interest Earned: ${results['total_interest']:,.2f}")


if __name__ == "__main__":
    main()
