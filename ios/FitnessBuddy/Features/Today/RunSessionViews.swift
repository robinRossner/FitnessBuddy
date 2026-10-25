import SwiftUI

/// "Z1–Z2" capsule coloured by the hottest zone it names; a bpm range ("127–156 bpm") when no zone can be derived.
struct HRZoneChipView: View {
    let hr: HRRangeDTO?
    var lthr: Double? = nil

    var body: some View {
        if let label = HRZoneChip.label(for: hr, lthr: lthr) {
            let color = HRZoneChip.highestZone(in: label).map(ChartPalette.zone) ?? Color.secondary
            Text(label)
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .padding(.horizontal, 10).padding(.vertical, 4)
                .background(color.opacity(0.22), in: Capsule())
                .overlay(Capsule().strokeBorder(color.opacity(0.5), lineWidth: 1))
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityLabel("Heart rate \(label.replacingOccurrences(of: "–", with: " to "))")
        }
    }
}

/// Small captioned value; wraps instead of truncating.
struct MetricBlock<Value: View>: View {
    let title: String
    @ViewBuilder var value: Value

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            value
        }
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Card body (easy and long runs)

struct EasyRunBody: View {
    let session: PrescriptionDTO?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 24) { distance; heartRate; Spacer(minLength: 0) }
                VStack(alignment: .leading, spacing: 12) { distance; heartRate }
            }
            if let pace = PlanFormat.pace(session?.pace) {
                MetricBlock(title: "Pace") {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(pace).metricStyle(.title3, weight: .semibold)
                        Text("/km").font(.footnote.weight(.medium)).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    @ViewBuilder private var distance: some View {
        if let km = session?.distanceKm {
            MetricBlock(title: "Distance") {
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text(Fmt.km(km)).metricStyle(.title2).foregroundStyle(Domain.running.color)
                    Text("km").font(.footnote.weight(.medium)).foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder private var heartRate: some View {
        if session?.hr?.zone != nil || (session?.hr?.low != nil && session?.hr?.high != nil) {
            MetricBlock(title: "Heart rate") { HRZoneChipView(hr: session?.hr) }
        }
    }
}

struct IntervalsBody: View {
    let session: PrescriptionDTO?

    var body: some View {
        if let st = session?.structure, let work = st.work {
            VStack(alignment: .leading, spacing: 12) {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) { reps(work); pace(work) }
                    VStack(alignment: .leading, spacing: 4) { reps(work); pace(work) }
                }
                FlowLayout(spacing: 20, lineSpacing: 12) {
                    if work.hr != nil { MetricBlock(title: "Work HR") { HRZoneChipView(hr: work.hr) } }
                    if let rec = st.recovery?.distanceM {
                        MetricBlock(title: "Recovery") {
                            Text("\(Fmt.number(rec, maxFraction: 0)) m jog").font(.subheadline.weight(.semibold))
                        }
                    }
                    if let total = session?.totalKm {
                        MetricBlock(title: "Total") { Text("\(Fmt.km(total)) km").font(.subheadline.weight(.semibold)) }
                    }
                }
                if let wu = st.warmup?.distanceKm, let cd = st.cooldown?.distanceKm {
                    Text("Warm-up \(Fmt.km(wu)) km · Cool-down \(Fmt.km(cd)) km easy")
                        .font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func reps(_ work: IntervalWorkDTO) -> some View {
        Text("\(work.reps ?? 0) × \(Fmt.number(work.distanceM ?? 0, maxFraction: 0)) m")
            .metricStyle(.title2).foregroundStyle(Domain.running.color)
            .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder private func pace(_ work: IntervalWorkDTO) -> some View {
        if let label = work.paceLabel {
            Text("@ \(label) /km").font(.title3.weight(.semibold).monospacedDigit())
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - Detail sheet

/// Opened by tapping a run card: the whole structure, HR and pace ranges, distance and notes.
struct RunDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    let item: PlanItemDTO

    private var session: PrescriptionDTO? { item.session }
    private var type: String { session?.type ?? item.kind }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    summary
                    if let st = session?.structure, type == "intervals" { structure(st) }
                    notes
                    Button {} label: {
                        Label("Send to watch (coming soon)", systemImage: "applewatch")
                            .frame(maxWidth: .infinity, minHeight: 34)
                    }
                    .buttonStyle(.bordered)
                    .disabled(true)
                    .accessibilityHint("Not available yet")
                }
                .padding(16)
            }
            .background(Color.screenBackground)
            .navigationTitle(item.title ?? PlanFormat.runTitle(type))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }

    private var summary: some View {
        Card(tint: Domain.running.color) {
            Text(AppClock.date(fromDay: item.date)?.formatted(.dateTime.weekday(.wide).day().month(.wide)) ?? item.date)
                .font(.subheadline).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 14) {
                if let km = session?.distanceKm ?? session?.totalKm {
                    row("Distance", "\(Fmt.km(km)) km")
                }
                if session?.hr != nil { hrRow("Heart rate", session?.hr) }
                if let pace = PlanFormat.pace(session?.pace) { row("Pace", "\(pace) /km") }
            }
        }
    }

    private func structure(_ st: IntervalStructureDTO) -> some View {
        Card {
            SectionLabel(title: "Structure", symbol: "list.number", color: Domain.running.color)
            VStack(alignment: .leading, spacing: 14) {
                if let wu = st.warmup { stepRow("Warm-up", wu.distanceKm.map { "\(Fmt.km($0)) km easy" } ?? "easy", hr: wu.hr) }
                if let work = st.work {
                    stepRow("Work", "\(work.reps ?? 0) × \(Fmt.number(work.distanceM ?? 0, maxFraction: 0)) m"
                            + (work.paceLabel.map { " @ \($0) /km" } ?? ""), hr: work.hr)
                }
                if let rec = st.recovery {
                    stepRow("Recovery", "\(rec.distanceM.map { Fmt.number($0, maxFraction: 0) + " m " } ?? "")\(rec.type ?? "jog")", hr: nil)
                }
                if let cd = st.cooldown { stepRow("Cool-down", cd.distanceKm.map { "\(Fmt.km($0)) km easy" } ?? "easy", hr: cd.hr) }
            }
        }
    }

    @ViewBuilder private var notes: some View {
        let lines = noteLines
        if !lines.isEmpty {
            Card {
                SectionLabel(title: "Notes", symbol: "text.alignleft", color: Domain.running.color)
                ForEach(lines, id: \.self) { Text($0).font(.subheadline).fixedSize(horizontal: false, vertical: true) }
            }
        }
    }

    private var noteLines: [String] {
        var lines: [String] = []
        for line in item.adjustments + item.notes + [session?.reason, session?.pace?.note].compactMap({ $0 }) where !line.isEmpty && !lines.contains(line) {
            lines.append(line)
        }
        return lines
    }

    private func row(_ title: String, _ value: String) -> some View {
        MetricBlock(title: title) { Text(value).metricStyle(.title3, weight: .semibold) }
    }

    private func hrRow(_ title: String, _ hr: HRRangeDTO?) -> some View {
        MetricBlock(title: title) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                HRZoneChipView(hr: hr)
                if let bpm = HRZoneChip.bpmText(hr) { Text(bpm).font(.subheadline.monospacedDigit()).foregroundStyle(.secondary) }
            }
        }
    }

    private func stepRow(_ title: String, _ text: String, hr: HRRangeDTO?) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(text).font(.subheadline.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            if hr != nil {
                HStack(spacing: 10) {
                    HRZoneChipView(hr: hr)
                    if let bpm = HRZoneChip.bpmText(hr) { Text(bpm).font(.footnote.monospacedDigit()).foregroundStyle(.secondary) }
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
}
