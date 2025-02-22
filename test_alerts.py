from alerts import AlertManager

def test_alerts():
    alert_manager = AlertManager()
    
    # Test entry alert
    entry_msg = alert_manager.format_entry_alert(
        event_id=84546,
        league="NBA",
        side_index=1,  # Home team
        direction="BUY_VOL",
        size=500.00,
        confidence=0.85,
        live_vol=12.5,
        expected_vol=10.0,
        score_diff=+3.5,
        current_prob=0.650,
        game_clock="8:45 2Q",
        home_team="Warriors",
        away_team="Lakers",
        home_price=115.50,
        away_price=95.25
    )
    
    print("Sending entry alert...")
    success = alert_manager.send_alert(entry_msg)
    print(f"Entry alert {'sent successfully' if success else 'failed'}")
    
    # Test exit alert
    exit_msg = alert_manager.format_exit_alert(
        event_id=84546,
        league="NBA",
        side_index=1,
        position_type="BUY_VOL",
        reason="MEAN_REVERSION",
        pnl=125.50,
        total_pnl=1250.75,
        live_vol=11.0,
        expected_vol=10.5,
        score_diff=+5.5,
        current_prob=0.680,
        game_clock="3:15 2Q",
        home_team="Warriors",
        away_team="Lakers",
        home_price=122.75,
        away_price=88.50
    )
    
    print("\nSending exit alert...")
    success = alert_manager.send_alert(exit_msg)
    print(f"Exit alert {'sent successfully' if success else 'failed'}")

if __name__ == "__main__":
    test_alerts() 