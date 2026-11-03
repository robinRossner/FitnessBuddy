import FitnessCore
import SwiftUI

/// Lays children out left to right and wraps to the next line, so chips never truncate at large Dynamic Type.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        arrange(width: proposal.width ?? .infinity, subviews: subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = arrange(width: bounds.width, subviews: subviews)
        for (i, frame) in result.frames.enumerated() {
            subviews[i].place(at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                              proposal: ProposedViewSize(width: frame.width, height: frame.height))
        }
    }

    private func arrange(width: CGFloat, subviews: Subviews) -> (size: CGSize, frames: [CGRect]) {
        var frames: [CGRect] = []
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0, maxX: CGFloat = 0
        for sub in subviews {
            var size = sub.sizeThatFits(ProposedViewSize(width: width, height: nil))
            size.width = min(size.width, width)
            if x > 0, x + size.width > width {
                x = 0; y += rowHeight + lineSpacing; rowHeight = 0
            }
            frames.append(CGRect(origin: CGPoint(x: x, y: y), size: size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            maxX = max(maxX, x - spacing)
        }
        return (CGSize(width: maxX, height: y + rowHeight), frames)
    }
}

extension MetricStatus {
    var color: Color {
        switch self {
        case .ok: .green
        case .warn: .orange
        case .bad: .red
        case .unknown: .gray
        }
    }
}

/// One compact metric: "HRV 66 ms ↓4%", tinted by status.
struct ReadinessChip: View {
    let metric: ReadinessMetricDTO

    var body: some View {
        let status = metric.resolvedStatus
        HStack(spacing: 4) {
            Text("\(metric.chipLabel) \(metric.chipValue)").foregroundStyle(.primary)
            if let d = metric.chipDelta {
                Label(d.text, systemImage: d.up ? "arrow.up" : "arrow.down")
                    .labelStyle(.titleAndIcon)
                    .foregroundStyle(status == .unknown ? Color.secondary : status.color)
            }
        }
        .font(.caption.weight(.semibold).monospacedDigit())
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(status.color.opacity(0.18), in: Capsule())
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(metric.spokenDescription)
    }
}

/// The readiness card shared by Summary and Health (kit look): coloured header with (i), the headline with the level
/// symbol, the first explanation line and a row of metric chips. The full explanation lives in the sheet opened on tap.
struct ReadinessCard: View {
    let summary: ReadinessSummary?
    /// Grey context on the right of the header ("Today").
    var time: String?
    @State private var showDetails: Bool

    static let about = "Readiness compares this morning's HRV and resting heart rate and last night's sleep with your own baseline. It decides whether today's plan is kept, eased or swapped for recovery. Tap the card for the details."

    init(summary: ReadinessSummary?, time: String? = nil, startOpen: Bool = false) {
        self.summary = summary
        self.time = time
        _showDetails = State(initialValue: startOpen && summary != nil)
    }

    private var level: ReadinessLevel { summary?.level ?? .unknown }

    var body: some View {
        Card {
            HStack(spacing: 6) {
                Label("Readiness", systemImage: Domain.readiness.symbol)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Domain.readiness.color)
                InfoButton(text: Self.about)
                Spacer(minLength: 8)
                if let time, summary != nil { Text(time).font(.subheadline).foregroundStyle(.secondary) }
                if summary != nil { DisclosureChevron() }
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(summary?.headline ?? ReadinessLevel.unknown.title)
                    .font(.system(.title, design: .rounded).weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Image(systemName: "\(level.symbol).circle.fill")
                    .font(.title3)
                    .foregroundStyle(level.color)
                    .accessibilityHidden(true)
            }
            if let line = summary?.details.first {
                Text(line).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            } else if summary == nil {
                Text("Shows up once your watch data has synced.").font(.subheadline).foregroundStyle(.secondary)
            }
            if let metrics = summary?.metrics, !metrics.isEmpty {
                FlowLayout(spacing: 8, lineSpacing: 8) {
                    ForEach(metrics) { ReadinessChip(metric: $0) }
                }
            }
        }
        .onTapGesture { if summary != nil { showDetails = true } }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Readiness: \(summary?.headline ?? "no data yet")")
        .accessibilityValue((summary?.metrics ?? []).map(\.spokenDescription).joined(separator: ". "))
        .accessibilityHint(summary == nil ? "" : "Shows the details")
        .accessibilityAddTraits(summary == nil ? [] : .isButton)
        .accessibilityAction { if summary != nil { showDetails = true } }
        .sheet(isPresented: $showDetails) {
            if let summary { ReadinessDetailSheet(summary: summary) }
        }
    }
}

struct ReadinessRing: View {
    let level: ReadinessLevel
    var size: CGFloat = 68

    var body: some View {
        Ring(color: level.color, lineWidth: size / 7.5, dashed: level == .unknown) {
            Image(systemName: level.symbol)
                .font(.title2.weight(.bold))
                .foregroundStyle(level.color)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// Opened by tapping the card: every metric with its baseline, then the explanation lines.
struct ReadinessDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    let summary: ReadinessSummary

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 16) {
                        ReadinessRing(level: summary.level, size: 76)
                        Text(summary.headline).font(.title2.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    if !summary.metrics.isEmpty {
                        VStack(alignment: .leading, spacing: 0) {
                            SectionLabel(title: "Compared with your baseline", symbol: "waveform.path.ecg", color: Domain.recovery.color)
                                .padding(.bottom, 8)
                            ForEach(Array(summary.metrics.enumerated()), id: \.element.id) { i, m in
                                if i > 0 { Divider() }
                                MetricDetailRow(metric: m)
                            }
                        }
                        .padding(16)
                        .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                    }
                    if !summary.details.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            SectionLabel(title: "Why", symbol: "text.alignleft", color: Domain.recovery.color)
                            ForEach(summary.details, id: \.self) {
                                Text($0).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(16)
                        .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                    }
                }
                .padding(16)
            }
            .background(Color.screenBackground)
            .navigationTitle("Readiness")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() }.accessibilityIdentifier(AXID.Health.readinessDone) } }
        }
        .presentationDetents([.medium, .large])
    }
}

private struct MetricDetailRow: View {
    let metric: ReadinessMetricDTO

    var body: some View {
        let status = metric.resolvedStatus
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Image(systemName: status == .ok ? "checkmark.circle.fill" : (status == .unknown ? "circle.dashed" : "exclamationmark.circle.fill"))
                .foregroundStyle(status.color)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(metric.displayLabel).font(.subheadline.weight(.semibold))
                Text(caption).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Text(metric.valueWithUnit).metricStyle(.title3, weight: .semibold)
                .multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 10)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(metric.spokenDescription + (metric.baselineText.map { ", baseline \($0)" } ?? ""))
    }

    private var caption: String {
        var parts: [String] = []
        if let b = metric.baselineText { parts.append("Baseline \(b)") }
        if let p = metric.deltaPct { parts.append(Fmt.percent(p)) }
        parts.append(metric.resolvedStatus.word.capitalized)
        return parts.joined(separator: " · ")
    }
}
