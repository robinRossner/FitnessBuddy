import SwiftData
import SwiftUI

struct TodayView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \CachedPlannedSession.date) private var planned: [CachedPlannedSession]
    @Query private var states: [SyncState]
    @Query(filter: #Predicate<LocalSession> { $0.deletedAt == nil }, sort: \LocalSession.startedAt, order: .reverse)
    private var sessions: [LocalSession]
    @State private var showSettings = false

    private var today: String { AppClock.todayString }
    private var todayItems: [CachedPlannedSession] { planned.filter { $0.date == today } }
    private var activeSession: LocalSession? { sessions.first { $0.isActive } }

    private var readiness: ReadinessDTO? {
        if let state = states.first, state.readinessDate == today, let r = state.readiness { return r }
        return todayItems.compactMap { $0.item?.readiness }.first { $0.color != nil }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    ReadinessCard(readiness: readiness)
                    if todayItems.isEmpty {
                        EmptyPlanCard(isConfigured: env.settings.isConfigured) { showSettings = true }
                    } else {
                        ForEach(todayItems, id: \.id) { row in
                            if let item = row.item {
                                SessionCard(row: row, item: item, activeSession: activeSession,
                                            completed: completedSession(for: row))
                            }
                        }
                    }
                    WeekStrip(planned: planned, sessions: sessions, today: today)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Color.screenBackground)
            .navigationTitle("Today")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { SyncStatusBadge() }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
            }
            .refreshable { await env.sync.sync(.pullToRefresh) }
            .sheet(isPresented: $showSettings) { SettingsView() }
        }
    }

    private func completedSession(for row: CachedPlannedSession) -> LocalSession? {
        sessions.first { $0.endedAt != nil && ($0.plannedSessionId == row.id) }
    }
}

// MARK: - Readiness

struct ReadinessCard: View {
    let readiness: ReadinessDTO?

    private var level: ReadinessLevel { readiness?.level ?? .unknown }

    var body: some View {
        Card {
            HStack(spacing: 16) {
                Ring(color: level.color, lineWidth: 9, dashed: level == .unknown) {
                    Image(systemName: level.symbol)
                        .font(.title2.weight(.bold))
                        .foregroundStyle(level.color)
                }
                .frame(width: 68, height: 68)
                .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 4) {
                    SectionLabel(title: "Readiness", color: Domain.recovery.color)
                    Text(level.title).font(.title3.weight(.semibold))
                    Text(readiness?.reason ?? "Readiness shows up once your watch data has synced.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                }
                Spacer(minLength: 0)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Readiness \(level.rawValue). \(readiness?.reason ?? "")")
    }
}

struct EmptyPlanCard: View {
    let isConfigured: Bool
    let openSettings: () -> Void

    var body: some View {
        Card {
            Label(isConfigured ? "No plan cached yet" : "Connect your backend", systemImage: "icloud.and.arrow.down")
                .font(.headline)
            Text(isConfigured ? "Pull down to sync. Once a plan has been fetched it stays available offline."
                              : "Add the backend URL and API token in Settings to load your weekly plan.")
                .font(.subheadline).foregroundStyle(.secondary)
            if !isConfigured {
                Button("Open Settings", action: openSettings).buttonStyle(.borderedProminent)
            }
        }
    }
}

// MARK: - Session card

struct SessionCard: View {
    @Environment(AppEnvironment.self) private var env
    let row: CachedPlannedSession
    let item: PlanItemDTO
    let activeSession: LocalSession?
    let completed: LocalSession?

    private var domain: Domain { Domain.forKind(item.kind) }
    private var type: String { item.session?.type ?? item.kind }

    var body: some View {
        Card(tint: domain.color) {
            header
            if !item.adjustments.isEmpty { AdjustmentNote(lines: item.adjustments, level: item.readiness?.level ?? .unknown) }
            switch type {
            case "lifting": LiftingBody(session: item.session)
            case "intervals": IntervalsBody(session: item.session)
            case "easy_run", "long_run": EasyRunBody(session: item.session)
            default: RestBody(notes: item.notes, optional: item.session?.optional ?? false)
            }
            if type == "lifting" { startButton }
            if type != "lifting", type != "rest", !item.notes.isEmpty {
                ForEach(item.notes, id: \.self) { Text($0).font(.footnote).foregroundStyle(.secondary) }
            }
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: PlanFormat.symbol(forKind: item.kind)).foregroundStyle(domain.color)
                .accessibilityHidden(true)
            Text(item.title ?? PlanFormat.runTitle(type)).font(.title3.weight(.semibold))
            Spacer()
            if let gym = item.session?.gym, type == "lifting" { Chip(text: gym, color: domain.color) }
            if item.session?.deload?.active == true { Chip(text: "Deload", color: .purple) }
        }
    }

    @ViewBuilder private var startButton: some View {
        if let completed {
            Label("Done · \(Fmt.longDuration((completed.endedAt ?? .now).timeIntervalSince(completed.startedAt)))",
                  systemImage: "checkmark.circle.fill")
                .font(.subheadline.weight(.semibold)).foregroundStyle(.green)
        } else if activeSession != nil {
            Button { env.selectedTab = .workout } label: {
                Label("Resume workout", systemImage: "play.fill").frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.borderedProminent).tint(domain.color)
        } else {
            Button { env.startWorkout(from: row) } label: {
                Label("Start workout", systemImage: "play.fill").frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.borderedProminent).tint(domain.color)
            .disabled((item.session?.exercises ?? []).allSatisfy { $0.variantId == nil })
        }
    }
}

struct AdjustmentNote: View {
    let lines: [String]
    let level: ReadinessLevel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(lines, id: \.self) { line in
                Label(line, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.primary)
                    .labelStyle(.titleAndIcon)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background((level == .unknown ? Color.orange : level.color).opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct LiftingBody: View {
    let session: PrescriptionDTO?

    var body: some View {
        let exercises = session?.exercises ?? []
        VStack(spacing: 0) {
            ForEach(Array(exercises.enumerated()), id: \.offset) { index, ex in
                if index > 0 { Divider() }
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(ex.name.withoutGymSuffix).font(.subheadline.weight(.medium))
                        if ex.variantId == nil {
                            Text(ex.reason ?? "Log it once to get targets").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 8)
                    Text(PlanFormat.setsSummary(ex.sets))
                        .font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                        .multilineTextAlignment(.trailing)
                }
                .padding(.vertical, 8)
            }
        }
    }
}

struct EasyRunBody: View {
    let session: PrescriptionDTO?

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            if let km = session?.distanceKm { StatTile(title: "Distance", value: Fmt.km(km), unit: "km", color: Domain.running.color) }
            if let hr = PlanFormat.hr(session?.hr) { StatTile(title: "Heart rate", value: hr, unit: "bpm") }
            if let pace = PlanFormat.pace(session?.pace) { StatTile(title: "Pace", value: pace, unit: "/km") }
        }
        if let note = session?.pace?.note { Text(note).font(.footnote).foregroundStyle(.secondary) }
    }
}

struct IntervalsBody: View {
    let session: PrescriptionDTO?

    var body: some View {
        if let st = session?.structure, let work = st.work {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text("\(work.reps ?? 0) × \(Fmt.number(work.distanceM ?? 0, maxFraction: 0)) m")
                        .metricStyle(.title).foregroundStyle(Domain.running.color)
                    if let label = work.paceLabel {
                        Text("@ \(label) /km").font(.title3.weight(.semibold).monospacedDigit())
                    }
                }
                .minimumScaleFactor(0.7).lineLimit(1)
                HStack(alignment: .top, spacing: 16) {
                    if let hr = PlanFormat.hr(work.hr) { StatTile(title: "Work HR", value: hr, unit: "bpm") }
                    if let rec = st.recovery?.distanceM {
                        StatTile(title: "Recovery", value: Fmt.number(rec, maxFraction: 0), unit: "m jog")
                    }
                    if let total = session?.totalKm { StatTile(title: "Total", value: Fmt.km(total), unit: "km") }
                }
                if let wu = st.warmup?.distanceKm, let cd = st.cooldown?.distanceKm {
                    Text("Warm-up \(Fmt.km(wu)) km · Cool-down \(Fmt.km(cd)) km easy")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct RestBody: View {
    let notes: [String]
    let optional: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(optional ? "Optional rest day. Easy movement is fine." : "Rest day. Recover, eat, sleep.")
                .font(.subheadline).foregroundStyle(.secondary)
            ForEach(notes, id: \.self) { Text($0).font(.footnote).foregroundStyle(.secondary) }
        }
    }
}

// MARK: - Week strip

struct WeekStrip: View {
    let planned: [CachedPlannedSession]
    let sessions: [LocalSession]
    let today: String

    var body: some View {
        let days = AppClock.weekDays(containing: AppClock.now)
        Card {
            SectionLabel(title: "This week", symbol: "calendar")
            HStack(spacing: 4) {
                ForEach(days, id: \.self) { day in
                    DayCell(day: day, rows: planned.filter { $0.date == day }, isToday: day == today,
                            isDone: { isDone($0) })
                }
            }
        }
    }

    private func isDone(_ row: CachedPlannedSession) -> Bool {
        if row.status == "done" { return true }
        return sessions.contains { $0.endedAt != nil && $0.plannedSessionId == row.id }
    }
}

private struct DayCell: View {
    let day: String
    let rows: [CachedPlannedSession]
    let isToday: Bool
    let isDone: (CachedPlannedSession) -> Bool

    private var weekday: String {
        guard let d = AppClock.date(fromDay: day) else { return "" }
        return d.formatted(.dateTime.weekday(.narrow))
    }
    private var dayNumber: String { String(day.suffix(2)) }
    private var first: CachedPlannedSession? { rows.first }

    var body: some View {
        VStack(spacing: 6) {
            Text(weekday).font(.caption2.weight(.semibold)).foregroundStyle(isToday ? Color.white : .secondary)
            ZStack(alignment: .bottomTrailing) {
                Image(systemName: PlanFormat.symbol(forKind: first?.kind ?? "rest"))
                    .font(.body)
                    .foregroundStyle(isToday ? Color.white : (first.map { Domain.forKind($0.kind).color } ?? Color.secondary.opacity(0.5)))
                    .frame(width: 32, height: 32)
                    .background(isToday ? Color.white.opacity(0.25)
                                        : (first.map { Domain.forKind($0.kind).color } ?? .gray).opacity(0.14), in: Circle())
                if let first, isDone(first) {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.white, .green)
                        .offset(x: 4, y: 4)
                }
            }
            Text(PlanFormat.shortName(forKind: first?.kind ?? "rest"))
                .font(.caption2.weight(.medium))
                .foregroundStyle(isToday ? Color.white : .secondary)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(isToday ? Color.accentColor : .clear, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(day), \(first.map { PlanFormat.shortName(forKind: $0.kind) } ?? "no plan")\((first.map(isDone) ?? false) ? ", done" : "")")
    }
}

#if DEBUG
#Preview("Today") {
    let env = PreviewData.environment()
    return TodayView().environment(env).modelContainer(env.container)
}
#endif
