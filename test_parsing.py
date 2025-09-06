#!/usr/bin/env python3
"""
Test the field status parsing logic against historical content
"""

import json
import sys
import os

# Add the current directory to the Python path so we can import the bot
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from field_status_bot import FieldStatusBot

def test_parsing_logic():
    """Test the parsing logic against all historical entries"""
    bot = FieldStatusBot()
    
    # Load the history file
    with open('field_status_history.json', 'r') as f:
        data = json.load(f)
    
    history_entries = data.get('history', [])
    
    print("Testing Field Status Parsing Logic")
    print("=" * 50)
    print()
    
    passed = 0
    failed = 0
    
    for i, entry in enumerate(history_entries):
        content = entry['content']
        expected_status = entry['status']
        expected_closed_fields = entry['closed_fields']
        expected_contains_soccer = entry['contains_soccer']
        
        # Parse using the bot's logic
        actual_status, actual_closed_fields, actual_contains_soccer = bot.parse_field_status(content)
        
        # Compare results
        status_match = actual_status == expected_status
        fields_match = set(actual_closed_fields) == set(expected_closed_fields)
        soccer_match = actual_contains_soccer == expected_contains_soccer
        
        all_match = status_match and fields_match and soccer_match
        
        if all_match:
            passed += 1
            result = "PASS"
        else:
            failed += 1
            result = "FAIL"
        
        print(f"Test {i+1}: {result}")
        print(f"  Content: {content[:80]}{'...' if len(content) > 80 else ''}")
        print(f"  Expected: {expected_status}, {expected_closed_fields}, soccer={expected_contains_soccer}")
        print(f"  Actual:   {actual_status}, {actual_closed_fields}, soccer={actual_contains_soccer}")
        
        if not all_match:
            print(f"  Issues:")
            if not status_match:
                print(f"    - Status mismatch: expected '{expected_status}', got '{actual_status}'")
            if not fields_match:
                print(f"    - Fields mismatch: expected {expected_closed_fields}, got {actual_closed_fields}")
            if not soccer_match:
                print(f"    - Soccer flag mismatch: expected {expected_contains_soccer}, got {actual_contains_soccer}")
        
        print()
    
    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    print(f"Success rate: {passed/(passed+failed)*100:.1f}%")
    
    if failed > 0:
        print("\nRecommendations:")
        print("- Review parsing logic in parse_field_status() method")
        print("- Check field name patterns and closure detection logic")
        print("- Validate soccer field detection")

if __name__ == "__main__":
    test_parsing_logic()