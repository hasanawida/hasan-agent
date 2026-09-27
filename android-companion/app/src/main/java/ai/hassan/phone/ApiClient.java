package ai.hassan.phone;

import org.json.JSONObject;
import javax.net.ssl.HttpsURLConnection;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

final class ApiClient implements AutoCloseable {
    static final class HttpError extends IOException {
        final int status;
        HttpError(int status) { super("HTTP " + status); this.status = status; }
    }
    private final String origin;
    private final String token;
    private final Map<HttpsURLConnection, String> requests = new ConcurrentHashMap<>();
    private volatile boolean closed;
    static String validateOrigin(String value) throws Exception {
        URI uri = new URI(value.trim());
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null
            || uri.getQuery() != null || uri.getFragment() != null
            || !(uri.getPath() == null || uri.getPath().isEmpty() || "/".equals(uri.getPath()))
            || uri.getPort() == 0 || uri.getPort() < -1 || uri.getPort() > 65535)
            throw new IllegalArgumentException("أدخل رابط HTTPS الأساسي بدون مسار أو كلمة سر");
        return new URI("https", null, uri.getHost(), uri.getPort(), null, null, null).toASCIIString();
    }
    ApiClient(String origin, String token) throws Exception { this.origin = validateOrigin(origin); this.token = token; }
    JSONObject json(String path, JSONObject body, int timeoutMs) throws Exception {
        byte[] reply = request(path, body.toString().getBytes(StandardCharsets.UTF_8), "application/json", timeoutMs);
        return reply.length == 0 ? new JSONObject() : new JSONObject(new String(reply, StandardCharsets.UTF_8));
    }
    void frame(byte[] jpeg) throws Exception {
        if (jpeg.length > 1024 * 1024) throw new IOException("Frame exceeds limit");
        request("/api/phone/frame", jpeg, "image/jpeg", 8000);
    }
    private byte[] request(String path, byte[] body, String contentType, int timeoutMs) throws Exception {
        if (!path.startsWith("/api/phone/") || path.contains("..") || closed) throw new IOException("Session closed");
        HttpsURLConnection connection = (HttpsURLConnection) new URL(origin + path).openConnection();
        requests.put(connection, path);
        try {
            if (closed) throw new IOException("Session closed");
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(8000);
            connection.setReadTimeout(timeoutMs);
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", contentType);
            connection.setRequestProperty("Accept", "application/json");
            if (token != null && !token.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setFixedLengthStreamingMode(body.length);
            try (java.io.OutputStream out = connection.getOutputStream()) { out.write(body); }
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) throw new HttpError(status);
            try (InputStream in = connection.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = in.read(buffer)) != -1) {
                    if (out.size() + count > 256 * 1024) throw new IOException("Reply exceeds limit");
                    out.write(buffer, 0, count);
                }
                return out.toByteArray();
            }
        } finally { requests.remove(connection); connection.disconnect(); }
    }
    void cancelPoll() {
        for (Map.Entry<HttpsURLConnection, String> request : requests.entrySet()) {
            if ("/api/phone/poll".equals(request.getValue())) request.getKey().disconnect();
        }
    }
    @Override public void close() { closed = true; for (HttpsURLConnection request : requests.keySet()) request.disconnect(); requests.clear(); }
}
