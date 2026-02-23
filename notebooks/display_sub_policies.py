#!/usr/bin/env python3
"""
Display the expanded sub-policies dataframe in a readable format.
"""

import pandas as pd
from tabulate import tabulate

def display_sub_policies():
    """Display the expanded sub-policies dataframe."""

    # Read the expanded sub-policies
    df = pd.read_csv("expanded_sub_policies.csv")

    print("═" * 120)
    print("📋 EXPANDED SUB-POLICIES DATAFRAME")
    print("═" * 120)
    print(f"Total sub-policies: {len(df)}")
    print(f"Parent initiatives: {df['parent_initiative_name'].nunique()}")
    print()

    # Summary statistics
    print("📊 SUB-POLICY STRENGTH DISTRIBUTION:")
    strength_counts = df['sub_policy_strength'].value_counts()
    for strength, count in strength_counts.items():
        print(f"  {strength.capitalize()}: {count}")
    print()

    print("🏆 SUB-POLICIES BY PARENT INITIATIVE:")
    print()

    # Group by parent initiative and display
    for parent_name, group in df.groupby('parent_initiative_name'):
        initiative_result = group['initiative_result'].iloc[0]
        sector = group['sector'].iloc[0]

        print(f"🎯 {parent_name}")
        print(f"   📂 Sector: {sector} | 🎖️ Result: {initiative_result} | 📊 Sub-policies: {len(group)}")
        print()

        # Display sub-policies in a table
        sub_table = group[['sub_policy_action_label', 'sub_policy_strength', 'sub_policy_quantifiable_target', 'sub_policy_has_timeline']].copy()
        sub_table.columns = ['Action', 'Strength', 'Target', 'Timeline']

        print(tabulate(sub_table, headers='keys', tablefmt='grid', showindex=False))
        print()

if __name__ == "__main__":
    display_sub_policies()