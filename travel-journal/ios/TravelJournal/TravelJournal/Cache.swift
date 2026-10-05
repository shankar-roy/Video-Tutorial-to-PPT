import Foundation
import SwiftData

@Model
final class CachedHome {
    var city: String
    var latitude: Double
    var longitude: Double
    var radiusKm: Double

    init(city: String, latitude: Double, longitude: Double, radiusKm: Double) {
        self.city = city
        self.latitude = latitude
        self.longitude = longitude
        self.radiusKm = radiusKm
    }
}

@Model
final class CachedStayRecord {
    @Attribute(.unique) var clientId: String
    var payload: Data

    init(clientId: String, payload: Data) {
        self.clientId = clientId
        self.payload = payload
    }
}

@Model
final class CachedTripRecord {
    @Attribute(.unique) var tripId: Int
    var title: String
    var payload: Data

    init(tripId: Int, title: String, payload: Data) {
        self.tripId = tripId
        self.title = title
        self.payload = payload
    }
}

enum JournalCache {
    static func save(stays: [StayCluster], home: HomeConfirmation?, context: ModelContext) {
        let existing = (try? context.fetch(FetchDescriptor<CachedStayRecord>())) ?? []
        existing.forEach { context.delete($0) }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        for stay in stays {
            guard let data = try? encoder.encode(stay) else { continue }
            context.insert(CachedStayRecord(clientId: stay.id, payload: data))
        }
        let homes = (try? context.fetch(FetchDescriptor<CachedHome>())) ?? []
        homes.forEach { context.delete($0) }
        if let home {
            context.insert(CachedHome(city: home.city, latitude: home.latitude, longitude: home.longitude, radiusKm: home.radiusKm))
        }
        try? context.save()
    }

    static func loadStays(context: ModelContext) -> [StayCluster] {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let rows = (try? context.fetch(FetchDescriptor<CachedStayRecord>())) ?? []
        return rows.compactMap { try? decoder.decode(StayCluster.self, from: $0.payload) }
            .sorted { $0.startAt < $1.startAt }
    }

    static func loadHome(context: ModelContext) -> HomeConfirmation? {
        guard let row = try? context.fetch(FetchDescriptor<CachedHome>()).first else { return nil }
        return HomeConfirmation(city: row.city, latitude: row.latitude, longitude: row.longitude, radiusKm: row.radiusKm)
    }

    static func save(trip: TripDetailDTO, context: ModelContext) {
        guard let data = try? JSONCoding.encoder.encode(trip) else { return }
        let rows = (try? context.fetch(FetchDescriptor<CachedTripRecord>())) ?? []
        if let existing = rows.first(where: { $0.tripId == trip.id }) {
            existing.title = trip.title
            existing.payload = data
        } else {
            context.insert(CachedTripRecord(tripId: trip.id, title: trip.title, payload: data))
        }
        try? context.save()
    }

    static func loadTrip(id: Int, context: ModelContext) -> TripDetailDTO? {
        let rows = (try? context.fetch(FetchDescriptor<CachedTripRecord>())) ?? []
        guard let row = rows.first(where: { $0.tripId == id }) else { return nil }
        return try? JSONCoding.decoder.decode(TripDetailDTO.self, from: row.payload)
    }
}

extension JSONCoding {
    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()
}
