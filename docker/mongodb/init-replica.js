// MongoDB replica set initialisation script.
// Executed by docker-entrypoint-initdb.d on first container start.
// Initialises a single-node replica set (rs0) required for transactions.
//
// Architecture: V3 Ch5 (MongoDB transactions require replica set).

try {
    rs.status();
} catch (e) {
    rs.initiate({
        _id: "rs0",
        members: [{ _id: 0, host: "localhost:27017" }],
    });
}
