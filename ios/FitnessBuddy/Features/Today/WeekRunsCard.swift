import FitnessCore
import SwiftUI

/// "This week's running": the week's planned runs (plus moved and make-up ones) with their status, and a quiet
/// one-line summary of the lifting days. Tapping a run opens the run detail sheet.
struct WeekRunsCard: View {
    let model: WeekRunsModel
    let today: String
    @State private var selected: WeekRunRow?

    init(model: WeekRunsModel, today: String, openFirstAdaptive: Bool = false) {
        self.model = model
        self.today = today
        _selected = State(initialValue: openFirstAdaptive ? model.rows.first { $0.asterisk } : nil)
    }

    var body: some View {
        if !model.rows.isEmpty || model.liftLine != nil {
            Card {
                CardHeader(title: "This Week's Running", symbol: "figure.run", color: Domain.running.color)
                if !model.rows.isEmpty {
                    VStack(spacing: 0) {
                        ForEach(Array(model.rows.enumerated()), id: \.element.id) { index, row in
                            if index > 0 { Divider() }
                            Button { selected = row } label: { WeekRunRowView(row: row) }
                                .buttonStyle(.plain)
                                .accessibilityHint("Shows the full session")
                        }
                    }
                    if model.showsFootnote {
                        Text(WeekRunsModel.footnote)
                            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                if let line = model.liftLine {
                    if !model.rows.isEmpty { Divider() }
                    Label {
                        Text(line).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    } icon: {
                        Image(systemName: "dumbbell.fill").font(.footnote).foregroundStyle(Domain.lifting.color)
                    }
                    .accessibilityLabel("Lifting this week: \(line)")
                }
            }
            .sheet(item: $selected) { RunDetailSheet(item: $0.item) }
        }
    }
}

extension WeekRunRow {
    /// Easy green, intervals orange, long teal.
    var color: Color {
        switch kind {
        case "easy_run": .green
        case "intervals": .orange
        default: Domain.running.color
        }
    }
}

private struct WeekRunRowView: View {
    let row: WeekRunRow

    private var struck: Bool { row.state == .missed || row.state == .skipped }
    private var isToday: Bool { row.state == .today }

    private var detail: String {
        [row.distanceText, row.structureText].compactMap { $0 }.joined(separator: " · ")
    }

    private var note: String? {
        var parts: [String] = []
        if let from = row.movedFrom { parts.append("moved from \(DayLabel.weekday(from))") }
        if let ran = row.ranOn { parts.append("ran \(DayLabel.weekday(ran))") }
        if row.state == .skipped { parts.append("skipped") }
        if row.state == .missed { parts.append("missed") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    var body: some View {
        HStack(spacing: 12) {
            Text(DayLabel.weekday(row.date))
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(isToday ? Color.accentColor : .secondary)
                .frame(width: 36, alignment: .leading)
            Image(systemName: PlanFormat.symbol(forKind: row.kind))
                .font(.subheadline)
                .foregroundStyle(row.color)
                .frame(width: 30, height: 30)
                .background(row.color.opacity(struck ? 0.08 : 0.15), in: Circle())
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(row.title + (row.asteriskOnTitle ? "*" : ""))
                        .font(.subheadline.weight(.semibold))
                        .strikethrough(struck, color: .secondary)
                    if isToday { Chip(text: "Today", color: .accentColor) }
                }
                if !detail.isEmpty {
                    Text(detail)
                        .font(.footnote.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .strikethrough(struck, color: .secondary)
                }
                if let note { Text(note).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 4)
            trailing
        }
        .padding(.vertical, 9)
        .opacity(struck ? 0.65 : 1)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var trailing: some View {
        HStack(spacing: 8) {
            if row.state != .done {
                if row.hrLabel != nil {
                    HRZoneChipView(hr: row.item.session?.hr ?? row.item.session?.structure?.work?.hr, compact: true)
                } else if let pace = row.paceText {
                    Text(pace + "/km").font(.caption.weight(.medium).monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            if row.state == .done {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).accessibilityLabel("Done")
            } else {
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                    .accessibilityHidden(true)
            }
        }
    }
}
