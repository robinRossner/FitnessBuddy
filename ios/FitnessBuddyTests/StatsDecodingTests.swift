import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy
@testable import FitnessCore

/// The DEBUG demo stats decode with the real DTOs.
@Suite(.tags(.stats, .decoding, .fast))
struct DemoStatsDecodingTests {
    @Test func demoStatsDecode() throws {
        for path in ["/api/v1/stats/lifts", "/api/v1/stats/lifts/101", "/api/v1/stats/volume/weekly", "/api/v1/stats/running",
                     "/api/v1/runs/run-i0/analysis", "/api/v1/runs", "/api/v1/stats/health", "/api/v1/stats/correlations"] {
            let data = try #require(DemoStats.json(for: path), "no demo data for \(path)")
            #expect(!data.isEmpty)
        }
        let d = try APICoding.makeDecoder().decode(LiftDetailDTO.self, from: try #require(DemoStats.json(for: "/api/v1/stats/lifts/101")))
        #expect(d.points.count > 50 && d.lift.variantId == 101)
        // Digit-bearing keys (trend_4w_pct, hrv_7d, ...) also round-trip through the encoder used for the demo data.
        #expect(d.lift.trend4wPct != nil)
        let h = try APICoding.makeDecoder().decode(HealthStatsDTO.self, from: try #require(DemoStats.json(for: "/api/v1/stats/health")))
        #expect(h.days.count == 90 && h.summary?.hrv7d != nil && h.summary?.sleep28dS != nil && h.summary?.bodyweightChange4wPct != nil)
    }
}
