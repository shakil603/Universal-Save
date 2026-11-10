package app;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import org.schabi.newpipe.extractor.Image;
import org.schabi.newpipe.extractor.InfoItem;
import org.schabi.newpipe.extractor.NewPipe;
import org.schabi.newpipe.extractor.ServiceList;
import org.schabi.newpipe.extractor.StreamingService;
import org.schabi.newpipe.extractor.downloader.Downloader;
import org.schabi.newpipe.extractor.downloader.Request;
import org.schabi.newpipe.extractor.downloader.Response;
import org.schabi.newpipe.extractor.exceptions.ReCaptchaException;
import org.schabi.newpipe.extractor.search.SearchInfo;
import org.schabi.newpipe.extractor.stream.StreamInfo;
import org.schabi.newpipe.extractor.stream.StreamInfoItem;
import org.schabi.newpipe.extractor.stream.VideoStream;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;

/**
 * Small internal HTTP service on 127.0.0.1 (only the Python app calls it).
 *
 *   GET /health
 *   GET /info?url=<video or channel URL>
 *   GET /info?q=<search keywords>        (YouTube search, first video)
 *
 * Responds with JSON: title, uploader, duration, thumbnail, platform and the
 * list of muxed (video+audio) streams, which the Python side can proxy directly.
 */
public final class NewPipeService {

    private static final String USER_AGENT =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

    private NewPipeService() {
    }

    /** Plain HttpURLConnection based downloader (no extra HTTP library needed). */
    static final class HttpDownloader extends Downloader {
        @Override
        public Response execute(Request request) throws IOException, ReCaptchaException {
            HttpURLConnection conn = (HttpURLConnection) new URL(request.url()).openConnection();
            conn.setRequestMethod(request.httpMethod());
            conn.setConnectTimeout(15_000);
            conn.setReadTimeout(30_000);
            conn.setInstanceFollowRedirects(true);

            boolean hasUa = false;
            for (Map.Entry<String, List<String>> header : request.headers().entrySet()) {
                if (header.getKey() == null) {
                    continue;
                }
                if ("User-Agent".equalsIgnoreCase(header.getKey())) {
                    hasUa = true;
                }
                for (String value : header.getValue()) {
                    conn.addRequestProperty(header.getKey(), value);
                }
            }
            if (!hasUa) {
                conn.setRequestProperty("User-Agent", USER_AGENT);
            }

            byte[] body = request.dataToSend();
            if (body != null) {
                conn.setDoOutput(true);
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(body);
                }
            }

            int code = conn.getResponseCode();
            InputStream in = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            String text = in == null ? "" : readAll(in);

            Map<String, List<String>> headers = new java.util.HashMap<>();
            for (Map.Entry<String, List<String>> h : conn.getHeaderFields().entrySet()) {
                if (h.getKey() != null) {
                    headers.put(h.getKey(), h.getValue());
                }
            }
            return new Response(code, conn.getResponseMessage(), headers, text, conn.getURL().toString());
        }

        private static String readAll(InputStream in) throws IOException {
            try (InputStream input = in) {
                ByteArrayOutputStream buffer = new ByteArrayOutputStream();
                byte[] chunk = new byte[8192];
                int n;
                while ((n = input.read(chunk)) != -1) {
                    buffer.write(chunk, 0, n);
                }
                return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
            }
        }
    }

    public static void main(String[] args) throws IOException {
        int port = Integer.parseInt(System.getenv().getOrDefault("NEWPIPE_PORT", "8081"));
        NewPipe.init(new HttpDownloader());

        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
        server.createContext("/health", ex -> sendJson(ex, 200, "{\"ok\":true}"));
        server.createContext("/info", NewPipeService::handleInfo);
        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
        System.out.println("NewPipe service listening on 127.0.0.1:" + port);
    }

    private static void handleInfo(HttpExchange ex) throws IOException {
        try {
            Map<String, String> params = parseQuery(ex.getRequestURI().getRawQuery());
            String url = params.get("url");
            String query = params.get("q");

            if (url == null && query == null) {
                sendJson(ex, 400, "{\"ok\":false,\"error\":\"url or q is required\"}");
                return;
            }
            if (url == null) {
                url = firstYouTubeVideoUrl(query);
                if (url == null) {
                    sendJson(ex, 404, "{\"ok\":false,\"error\":\"no video found\"}");
                    return;
                }
            }

            StreamingService service = NewPipe.getServiceByUrl(url);
            StreamInfo info = StreamInfo.getInfo(url);

            StringBuilder json = new StringBuilder();
            json.append("{\"ok\":true");
            json.append(",\"platform\":").append(quote(service.getServiceInfo().getName()));
            json.append(",\"title\":").append(quote(info.getName()));
            json.append(",\"uploader\":").append(quote(info.getUploaderName()));
            json.append(",\"duration\":").append(info.getDuration());
            json.append(",\"thumbnail\":").append(quote(firstThumbnail(info.getThumbnails())));
            json.append(",\"webpage_url\":").append(quote(info.getUrl()));
            json.append(",\"streams\":[");
            boolean first = true;
            for (VideoStream v : info.getVideoStreams()) {
                if (v.isVideoOnly() || v.getUrl() == null || v.getUrl().isEmpty()) {
                    continue;
                }
                if (!first) {
                    json.append(',');
                }
                first = false;
                json.append("{\"url\":").append(quote(v.getUrl()))
                        .append(",\"height\":").append(v.getHeight())
                        .append(",\"resolution\":").append(quote(v.getResolution()))
                        .append(",\"format\":").append(quote(v.getFormat() == null ? "" : v.getFormat().getName()))
                        .append('}');
            }
            json.append("]}");
            sendJson(ex, 200, json.toString());
        } catch (Exception e) {
            String message = e.getMessage() == null ? e.toString() : e.getMessage();
            System.err.println("info failed: " + message);
            sendJson(ex, 500, "{\"ok\":false,\"error\":" + quote(message) + "}");
        }
    }

    /** Uses NewPipe's YouTube search and returns the first video URL. */
    private static String firstYouTubeVideoUrl(String query) throws Exception {
        SearchInfo search = SearchInfo.getInfo(ServiceList.YouTube.getSearchExtractor(query));
        for (InfoItem item : search.getRelatedItems()) {
            if (item instanceof StreamInfoItem && item.getUrl() != null) {
                return item.getUrl();
            }
        }
        return null;
    }

    private static String firstThumbnail(List<Image> images) {
        if (images == null || images.isEmpty()) {
            return "";
        }
        String url = images.get(0).getUrl();
        return url == null ? "" : url;
    }

    private static Map<String, String> parseQuery(String raw) throws IOException {
        Map<String, String> out = new java.util.HashMap<>();
        if (raw == null || raw.isEmpty()) {
            return out;
        }
        for (String pair : raw.split("&")) {
            int eq = pair.indexOf('=');
            if (eq <= 0) {
                continue;
            }
            String key = URLDecoder.decode(pair.substring(0, eq), StandardCharsets.UTF_8);
            String value = URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8);
            out.put(key, value);
        }
        return out;
    }

    private static void sendJson(HttpExchange ex, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }

    /** Minimal JSON string encoder. */
    static String quote(String value) {
        if (value == null) {
            return "\"\"";
        }
        StringBuilder sb = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        return sb.append('"').toString();
    }
}
