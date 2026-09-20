// Read-only EventKit bridge. No save/remove APIs and no network access.
import Foundation
import EventKit
import AppKit

func output(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) { print(text) }
}
func permission() -> String {
    let status = EKEventStore.authorizationStatus(for: .event)
    if #available(macOS 14.0, *) {
        if status == .fullAccess { return "authorized" }
        if status == .writeOnly { return "write_only" }
    } else if status == .authorized { return "authorized" }
    if status == .notDetermined { return "not_determined" }
    return status == .restricted ? "restricted" : "denied"
}
let command = CommandLine.arguments.dropFirst().first ?? "status"
if command == "status" { output(["permission": permission()]); exit(0) }
let store = EKEventStore()
if command == "authorize" {
    if permission() == "not_determined" || permission() == "write_only" {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        var finished = false
        let completion: (Bool, Error?) -> Void = { _, _ in
            DispatchQueue.main.async { finished = true }
        }
        if #available(macOS 14.0, *) { store.requestFullAccessToEvents(completion: completion) }
        else { store.requestAccess(to: .event, completion: completion) }
        let deadline = Date().addingTimeInterval(90)
        while !finished && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
    }
    output(["permission": permission()]); exit(0)
}
guard permission() == "authorized" else { output(["error":"calendar_permission_required", "permission":permission()]); exit(1) }
let calendars = store.calendars(for: .event)
if command == "calendars" {
    output(["permission":permission(), "calendars":calendars.map { ["id":$0.calendarIdentifier, "title":$0.title, "account":$0.source.title] }]); exit(0)
}
guard command == "read",
      let args = try? JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as? [String:Any],
      let from = args["from"] as? Double, let to = args["to"] as? Double,
      to > from, to - from <= 370 * 86400,
      let ids = args["calendar_ids"] as? [String], !ids.isEmpty else {
    output(["error":"invalid_calendar_window"]); exit(1)
}
let selected = calendars.filter { ids.contains($0.calendarIdentifier) }
guard selected.count == Set(ids).count else { output(["error":"selected_calendar_unavailable"]); exit(1) }
let fmt = ISO8601DateFormatter()
let dateOnly = DateFormatter(); dateOnly.calendar = Calendar(identifier: .gregorian)
dateOnly.locale = Locale(identifier: "en_US_POSIX"); dateOnly.dateFormat = "yyyy-MM-dd"
let matches = store.events(matching: store.predicateForEvents(withStart: Date(timeIntervalSince1970: from), end: Date(timeIntervalSince1970: to), calendars: selected))
guard matches.count <= 20000 else { output(["error":"calendar_window_too_large"]); exit(1) }
let rows: [[String:Any]] = matches.map { event in
    dateOnly.timeZone = event.timeZone ?? TimeZone.current
    return ["uid":event.eventIdentifier ?? event.calendarItemIdentifier,
            "calendar_id":event.calendar.calendarIdentifier,
            "calendar_title":event.calendar.title,
            "title":event.title ?? "(untitled)", "location":event.location ?? "",
            "start_time":event.isAllDay ? dateOnly.string(from:event.startDate) : fmt.string(from:event.startDate),
            "end_time":event.isAllDay ? dateOnly.string(from:event.endDate) : fmt.string(from:event.endDate),
            "all_day":event.isAllDay]
}
output(["permission":permission(), "events":rows])
