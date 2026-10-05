import Foundation
import Security
import UIKit

struct MemberDTO: Codable, Equatable {
    var id: Int
    var displayName: String
    var email: String
    var householdId: Int
    var householdName: String
    var inviteCode: String
    var home: HomeDTO?
}

struct HomeDTO: Codable, Equatable {
    var city: String
    var latitude: Double
    var longitude: Double
    var radiusKm: Double
    var airportIata: String
}

struct AuthResponse: Codable {
    var sessionToken: String
    var member: MemberDTO
}

struct TripSummaryDTO: Codable, Identifiable, Equatable {
    var id: Int
    var title: String
    var startDate: String
    var endDate: String
    var primaryCity: String
    var status: String
    var memberIds: [Int]
}

struct TripDetailDTO: Codable {
    var id: Int
    var title: String
    var startDate: String
    var endDate: String
    var primaryCity: String
    var status: String
    var memberIds: [Int]
    var route: [RoutePointDTO]
    var days: [JournalDayDTO]
}

struct RoutePointDTO: Codable, Equatable {
    var name: String
    var latitude: Double
    var longitude: Double
    var kind: String
}

struct JournalDayDTO: Codable, Identifiable {
    var day: String
    var placeNames: [String]
    var body: String
    var draftBody: String
    var edited: Bool
    var flights: [FlightDTO]
    var lodgings: [LodgingDTO]
    var photos: [JournalPhotoDTO]
    var id: String { day }
}

struct FlightDTO: Codable, Equatable {
    var airline: String
    var flightNumber: String
    var originIata: String
    var destinationIata: String
    var departureAt: Date
    var arrivalAt: Date?
    var confirmationCode: String
}

struct LodgingDTO: Codable, Equatable {
    var name: String
    var address: String
    var confirmationCode: String
}

struct JournalPhotoDTO: Codable, Identifiable, Equatable {
    var id: Int
    var assetId: String
    var takenAt: Date
    var latitude: Double
    var longitude: Double
    var thumbnailUrl: String?
    var selected: Bool
}

struct MergeSuggestionDTO: Codable, Identifiable {
    var id: Int
    var tripIds: [Int]
    var titles: [String]
}

struct AssembleResponse: Codable {
    var trips: [TripSummaryDTO]
    var mergeSuggestions: [MergeSuggestionDTO]
}

struct GmailStart: Codable {
    var authorizationUrl: String
}

struct SyncStats: Codable {
    var flights: Int
    var lodgings: Int
    var failures: Int
}

enum APIError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self {
        case .message(let text): return text
        }
    }
}

final class APIClient {
    var token: String?

    var baseURL: URL {
        let raw = UserDefaults.standard.string(forKey: "apiBaseURL") ?? "http://127.0.0.1:8000"
        return URL(string: raw) ?? URL(string: "http://127.0.0.1:8000")!
    }

    func signInWithApple(identityToken: String, displayName: String?) async throws -> AuthResponse {
        try await send("/auth/apple", method: "POST", json: [
            "identity_token": identityToken,
            "display_name": displayName ?? "",
        ], auth: false)
    }

    func devSignIn(name: String) async throws -> AuthResponse {
        try await send("/auth/dev", method: "POST", json: ["sub": "iphone", "display_name": name], auth: false)
    }

    func putHome(_ home: HomeConfirmation) async throws -> MemberDTO {
        let body: [String: Any] = [
            "city": home.city,
            "latitude": home.latitude,
            "longitude": home.longitude,
            "radius_km": home.radiusKm,
        ]
        return try await send("/me/home", method: "PUT", json: body)
    }

    func syncStays(_ stays: [StayCluster]) async throws {
        let payload = stays.map { stay in
            [
                "client_id": stay.id,
                "start_at": ISO8601.string(from: stay.startAt),
                "end_at": ISO8601.string(from: stay.endAt),
                "latitude": stay.latitude,
                "longitude": stay.longitude,
                "place_name": stay.placeName,
                "photos": stay.photos.map { photo in
                    [
                        "asset_id": photo.assetId,
                        "taken_at": ISO8601.string(from: photo.takenAt),
                        "latitude": photo.latitude,
                        "longitude": photo.longitude,
                    ] as [String: Any]
                },
            ] as [String: Any]
        }
        let _: StayCount = try await send("/me/stays", method: "POST", json: ["stays": payload])
    }

    func gmailStart() async throws -> URL {
        let response: GmailStart = try await send("/gmail/start")
        guard let url = URL(string: response.authorizationUrl) else {
            throw APIError.message("Gmail did not return a sign-in link")
        }
        return url
    }

    func assemble() async throws -> AssembleResponse {
        try await send("/trips/assemble", method: "POST")
    }

    func trips() async throws -> [TripSummaryDTO] {
        try await send("/trips")
    }

    func trip(_ id: Int) async throws -> TripDetailDTO {
        try await send("/trips/\(id)")
    }

    func suggestions() async throws -> [MergeSuggestionDTO] {
        try await send("/merge-suggestions")
    }

    func acceptMerge(_ id: Int) async throws -> TripDetailDTO {
        try await send("/merge-suggestions/\(id)/accept", method: "POST")
    }

    func dismissMerge(_ id: Int) async throws {
        let _: StatusBody = try await send("/merge-suggestions/\(id)/dismiss", method: "POST")
    }

    func join(inviteCode: String) async throws -> MemberDTO {
        try await send("/households/join", method: "POST", json: ["invite_code": inviteCode])
    }

    func updateTitle(tripId: Int, title: String) async throws -> TripDetailDTO {
        try await send("/trips/\(tripId)", method: "PATCH", json: ["title": title])
    }

    func updateDay(tripId: Int, day: String, body: String) async throws -> TripDetailDTO {
        try await send("/trips/\(tripId)/days/\(day)", method: "PATCH", json: ["body": body])
    }

    func uploadThumbnail(tripId: Int, day: String, assetId: String, takenAt: Date, latitude: Double, longitude: Double, image: UIImage) async throws -> TripDetailDTO {
        guard let data = image.jpegData(compressionQuality: 0.8) else {
            throw APIError.message("Could not read that image")
        }
        var request = URLRequest(url: baseURL.appendingPathComponent("trips/\(tripId)/days/\(day)/photos"))
        request.httpMethod = "POST"
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        func field(_ name: String, _ value: String) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".data(using: .utf8)!)
            body.append("\(value)\r\n".data(using: .utf8)!)
        }
        field("asset_id", assetId)
        field("taken_at", ISO8601.string(from: takenAt))
        field("latitude", String(latitude))
        field("longitude", String(longitude))
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"thumb.jpg\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(data)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body
        return try await decode(request)
    }

    func fetchImage(_ path: String) async throws -> UIImage {
        let url = path.hasPrefix("http") ? URL(string: path)! : URL(string: path, relativeTo: baseURL)!.absoluteURL
        var request = URLRequest(url: url)
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode), let image = UIImage(data: data) else {
            throw APIError.message("Could not load the shared photo")
        }
        return image
    }

    private func send<T: Decodable>(_ path: String, method: String = "GET", json: Any? = nil, auth: Bool = true) async throws -> T {
        var request = URLRequest(url: url(path))
        request.httpMethod = method
        if auth, let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let json {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
        }
        return try await decode(request)
    }

    private func url(_ path: String) -> URL {
        var text = baseURL.absoluteString
        if text.hasSuffix("/") { text.removeLast() }
        let suffix = path.hasPrefix("/") ? path : "/" + path
        return URL(string: text + suffix) ?? baseURL
    }

    private func decode<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if !(200..<300).contains(status) {
            if let body = try? JSONDecoder().decode(DetailBody.self, from: data), let detail = body.detail {
                throw APIError.message(detail)
            }
            let text = String(data: data, encoding: .utf8) ?? "Request failed"
            throw APIError.message(text)
        }
        do {
            return try JSONCoding.decoder.decode(T.self, from: data)
        } catch {
            throw APIError.message("Could not read the server response")
        }
    }
}

private struct StayCount: Decodable {
    var stays: Int
}

private struct StatusBody: Decodable {
    var status: String
}

private struct DetailBody: Decodable {
    var detail: String?
}

enum JSONCoding {
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let value = try container.decode(String.self)
            if value.count == 10, let day = ISO8601.day(value) { return day }
            if let date = ISO8601.date(value) { return date }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: value)
        }
        return decoder
    }()
}

enum ISO8601 {
    static func string(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }

    static func date(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }

    static func day(_ value: String) -> Date? {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: value)
    }
}

enum KeychainStore {
    private static let account = "traveljournal.session"

    static func save(_ value: String) {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        var insert = query
        insert[kSecValueData as String] = data
        SecItemAdd(insert as CFDictionary, nil)
    }

    static func read() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    static func clear() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
