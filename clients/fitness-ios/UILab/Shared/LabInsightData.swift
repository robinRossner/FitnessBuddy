import Foundation

// Transcribed from docs/api/examples/stats_correlations.json (real example data).
extension LabData {
    static let insights: [LabInsight] = [
        LabInsight(
            id: "sleep_vs_lifting", category: .recovery,
            title: "Sleep vs lifting performance",
            xLabel: "Sleep the night before (h)", yLabel: "Best e1RM as % of the 8-week best",
            r: 0.582, n: 11,
            note: "n=11, moderate positive, not conclusive. Each lift is compared with its own best of the previous 8 weeks, averaged per day.",
            insight: "More sleep tends to go with better lifting days (r=0.58, n=11, early read)",
            points: LabInsight.points([(9.54, 100.8), (6.52, 100.0), (7.33, 100.3), (7.93, 100.0), (8.2, 100.0), (8.12, 100.0), (8.15, 100.0), (7.73, 100.0), (7.33, 100.0), (7.67, 100.0), (8.58, 100.0)])),
        LabInsight(
            id: "hrv_vs_lift_performance", category: .recovery,
            title: "HRV vs lifting performance",
            xLabel: "Overnight HRV (ms)", yLabel: "Best e1RM as % of the 8-week best",
            r: 0.434, n: 11,
            note: "n=11, moderate positive, not conclusive. Each lift is compared with its own best of the previous 8 weeks, averaged per day.",
            insight: "Higher HRV tends to go with better lifting days (r=0.43, n=11, early read)",
            points: LabInsight.points([(101.0, 100.8), (48.0, 100.0), (69.0, 100.3), (69.0, 100.0), (84.0, 100.0), (79.0, 100.0), (73.0, 100.0), (80.0, 100.0), (93.0, 100.0), (85.0, 100.0), (83.0, 100.0)])),
        LabInsight(
            id: "hrv_vs_easy_efficiency", category: .recovery,
            title: "HRV vs easy-run efficiency",
            xLabel: "Overnight HRV (ms)", yLabel: "Efficiency (m/min per bpm)",
            r: 0.661, n: 6,
            note: "n=6, strong positive, not conclusive. Easy runs only; efficiency is speed per heartbeat, so pace differences cancel out.",
            insight: "Higher HRV tends to go with more efficient easy runs (r=0.66, n=6, early read)",
            points: LabInsight.points([(104.0, 1.2948), (86.0, 1.3198), (100.0, 1.3296), (87.0, 1.273), (79.0, 1.1597), (85.0, 1.2683)])),
        LabInsight(
            id: "rhr_vs_easy_efficiency", category: .recovery,
            title: "Resting HR vs easy-run efficiency",
            xLabel: "Resting HR (bpm)", yLabel: "Efficiency (m/min per bpm)",
            r: -0.583, n: 6,
            note: "n=6, moderate negative, not conclusive. Easy runs only; efficiency is speed per heartbeat, so pace differences cancel out.",
            insight: "A higher resting HR tends to go with less efficient easy runs (r=-0.58, n=6, early read)",
            points: LabInsight.points([(39.0, 1.2948), (42.0, 1.3198), (40.0, 1.3296), (42.0, 1.273), (43.0, 1.1597), (43.0, 1.2683)])),
        LabInsight(
            id: "run_load_vs_lift_performance", category: .hybrid,
            title: "Running load vs lifting performance",
            xLabel: "Running in the 48 h before the session (km)", yLabel: "Best e1RM as % of the 8-week best",
            r: -0.124, n: 30,
            note: "n=30, negligible, correlation is not causation. Interference effect: runs that started up to 48 h before the first lift of the day. Each lift is compared with its own best of the previous 8 weeks, averaged per day.",
            insight: "No clear link between recent running and lifting performance (r=-0.12, n=30)",
            points: LabInsight.points([(0.0, 100.0), (0.0, 100.0), (0.0, 100.0), (0.0, 100.0), (0.0, 100.0), (0.0, 100.0), (0.0, 100.4), (0.0, 99.6), (0.0, 100.3), (0.0, 100.0), (0.0, 100.0), (0.0, 100.0), (0.0, 99.6), (0.0, 101.6), (0.0, 100.0), (0.0, 100.0), (0.0, 100.0), (0.0, 101.0), (0.0, 100.0), (0.0, 100.8), (0.0, 100.0), (5.22, 100.3), (6.4, 100.0), (12.1, 100.0), (5.25, 100.0), (7.22, 100.0), (0.0, 100.0), (10.25, 100.0), (0.0, 100.0), (8.13, 100.0)])),
    ]
}
