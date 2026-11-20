import FitnessCore
import SwiftUI

/// "Z1–Z2" capsule coloured by the hottest zone it names; a bpm range ("124–152 bpm") when no zone can be derived.
struct HRZoneChipView: View {
    let hr: HRRangeDTO?
    var lthr: Double? = nil
    /// Smaller chip for list rows.
    var compact = false

    var body: some View {
        if let label = HRZoneChip.label(for: hr, lthr: lthr) {
            let color = HRZoneChip.highestZone(in: label).map(ChartPalette.zone) ?? Color.secondary
            Text(label)
                .font((compact ? Font.caption : Font.subheadline).weight(.semibold).monospacedDigit())
                .padding(.horizontal, compact ? 8 : 10).padding(.vertical, compact ? 3 : 4)
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
    @Environment(AppEnvironment.self) private var env
    let item: PlanItemDTO
    @State private var sendState: SendState = .idle

    private var session: PrescriptionDTO? { item.session }
    private var type: String { session?.type ?? item.kind }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    summary
                    if let st = session?.structure, type == "intervals" { structure(st) }
                    whyItMayChange
                    notes
                    sendToWatch
                }
                .padding(16)
            }
            .background(Color.screenBackground)
            .navigationTitle(item.title ?? PlanFormat.runTitle(type))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .screenIdentifier(AXID.Progress.runDetail)
        .presentationDetents([.medium, .large])
    }

    // MARK: Send to watch (Garmin)

    enum SendState: Equatable {
        case idle, sending
        case sent(String)
        case failed(String)
    }

    /// Open runs from today on can be sent; past or finished ones cannot.
    private var canSend: Bool {
        item.date >= AppClock.todayString && item.planStatus != .done && item.planStatus != .skipped
            && env.settings.isConfigured
    }

    private var sendToWatch: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                Task { await send() }
            } label: {
                HStack(spacing: 8) {
                    if sendState == .sending { ProgressView().controlSize(.small) }
                    Label(sendState == .sending ? "Sending to watch…" : "Send to watch", systemImage: "applewatch")
                }
                .frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.bordered)
            .disabled(!canSend || sendState == .sending)
            .accessibilityHint("Uploads this run to Garmin Connect, scheduled on its day")
            switch sendState {
            case .sent(let text):
                Label(text, systemImage: "checkmark.circle.fill")
                    .font(.footnote).foregroundStyle(.green).fixedSize(horizontal: false, vertical: true)
            case .failed(let text):
                Label(text, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            default:
                EmptyView()
            }
            Text("Runs are sent to your Garmin watch automatically the evening before.")
                .font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func send() async {
        if SyncState.current(in: env.container.mainContext).demoActive {
            sendState = .failed("Not available with demo data.")
            return
        }
        guard let client = env.settings.makeClient() else {
            sendState = .failed(APIError.message(for: APIError.notConfigured))
            return
        }
        sendState = .sending
        do {
            let result = try await client.pushWorkoutsToGarmin(date: item.date)
            sendState = Self.outcome(of: result, on: item.date)
        } catch {
            sendState = .failed(APIError.message(for: error))
        }
    }

    static func outcome(of result: GarminPushResultDTO, on date: String) -> SendState {
        let items = result.items(on: date)
        if let failed = items.first(where: { $0.action == "failed" }) {
            return .failed("Garmin: \(failed.reason ?? "upload failed")")
        }
        guard let first = items.first else {
            return .failed("Nothing to send for this day (no open run).")
        }
        let name = first.name ?? "the run"
        if items.allSatisfy({ $0.action == "unchanged" }) {
            return .sent("Already on your watch as “\(name)”.")
        }
        return .sent("Sent as “\(name)”. Your watch gets it on its next sync with the phone.")
    }

    private var summary: some View {
        Card(tint: Domain.running.color) {
            Text(AppClock.date(fromDay: item.date).map { AppLocale.weekdayDate($0, month: .wide) } ?? item.date)
                .font(.subheadline).foregroundStyle(.secondary)
            if let from = item.movedFrom {
                Label("Moved from \(DayLabel.weekday(from))", systemImage: "arrow.uturn.right")
                    .font(.footnote.weight(.medium)).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 14) {
                if let km = session?.distanceKm ?? session?.totalKm {
                    row("Distance", "\(Fmt.km(km)) km")
                }
                if session?.hr != nil { hrRow("Heart rate", session?.hr) }
                if let pace = PlanFormat.pace(session?.pace) { row("Pace", "\(pace) /km") }
                if type != "intervals", let strides = PlanFormat.strides(session?.structure?.strides) {
                    MetricBlock(title: "Strides") {
                        Text(strides).metricStyle(.title3, weight: .semibold)
                        if let detail = PlanFormat.stridesDetail(session?.structure?.strides, session: session) {
                            Text(detail).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
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

    /// Only for sessions that can still change: what they depend on and why they last changed.
    @ViewBuilder private var whyItMayChange: some View {
        let keys = item.adaptsOn ?? []
        let reason = item.changeReason?.trimmingCharacters(in: .whitespaces) ?? ""
        let open = item.planStatus != .done && item.planStatus != .skipped
        if open, item.adaptive == true || !keys.isEmpty || !reason.isEmpty {
            Card {
                SectionLabel(title: "Why it may change", symbol: "arrow.triangle.2.circlepath", color: Domain.running.color)
                if !keys.isEmpty {
                    Text("This session can still change before you run it, based on:")
                        .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(keys, id: \.self) { key in
                            Label(PlanFormat.adaptsOnLabel(key), systemImage: "circle.fill")
                                .labelStyle(BulletLabelStyle())
                                .font(.subheadline)
                        }
                    }
                } else if reason.isEmpty {
                    Text("This session can still change based on your last run, readiness and weekly volume.")
                        .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if !reason.isEmpty {
                    MetricBlock(title: "Last change") {
                        Text(reason).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                    }
                }
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

/// Small dot instead of the SF Symbol glyph, for bullet lists.
private struct BulletLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle().fill(Domain.running.color).frame(width: 5, height: 5).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }
            configuration.title
        }
    }
}
