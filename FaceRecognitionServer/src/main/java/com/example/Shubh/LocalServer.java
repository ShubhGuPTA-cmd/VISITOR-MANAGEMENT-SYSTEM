package com.example.Shubh;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.*;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.concurrent.ConcurrentHashMap;

public class LocalServer {
    private static final Gson gson = new Gson();
    private static final ConcurrentHashMap<String, String> pendingRequests = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, String> approvalStatus = new ConcurrentHashMap<>();

    public static void main(String[] args) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress(8081), 0);

        server.createContext("/", new StaticFileHandler());
        server.createContext("/approval-request", new ApprovalRequestHandler());
        server.createContext("/approval-status", new ApprovalStatusHandler());
        server.createContext("/pending-requests", new PendingRequestsHandler());
        server.createContext("/respond", new RespondHandler());

        server.setExecutor(null);
        server.start();

        System.out.println("Server started on http://localhost:8081");
    }

    static class StaticFileHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String path = exchange.getRequestURI().getPath();
            if (path.equals("/")) path = "/index.html";

            try {
                String content = getStaticContent(path);
                String contentType = getContentType(path);

                exchange.getResponseHeaders().set("Content-Type", contentType);
                exchange.sendResponseHeaders(200, content.length());

                try (OutputStream os = exchange.getResponseBody()) {
                    os.write(content.getBytes());
                }
            } catch (Exception e) {
                String response = "File not found";
                exchange.sendResponseHeaders(404, response.length());
                try (OutputStream os = exchange.getResponseBody()) {
                    os.write(response.getBytes());
                }
            }
        }

        private String getStaticContent(String path) throws IOException {
            if (path.equals("/index.html")) {
                return getIndexHtml();
            } else if (path.equals("/style.css")) {
                return getStyleCss();
            } else if (path.equals("/script.js")) {
                return getScriptJs();
            }
            throw new FileNotFoundException();
        }

        private String getContentType(String path) {
            if (path.endsWith(".html")) return "text/html";
            if (path.endsWith(".css")) return "text/css";
            if (path.endsWith(".js")) return "application/javascript";
            return "text/plain";
        }
    }

    static class ApprovalRequestHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            if ("POST".equals(exchange.getRequestMethod())) {
                InputStreamReader isr = new InputStreamReader(exchange.getRequestBody());
                BufferedReader br = new BufferedReader(isr);
                String requestBody = br.readLine();

                JsonObject json = gson.fromJson(requestBody, JsonObject.class);
                String requestId = json.get("requestId").getAsString();
                String owner = json.get("owner").getAsString();
                String image = json.get("image").getAsString();

                pendingRequests.put(requestId, image);
                approvalStatus.put(requestId, "pending");

                String response = "Request received";
                exchange.sendResponseHeaders(200, response.length());
                try (OutputStream os = exchange.getResponseBody()) {
                    os.write(response.getBytes());
                }
            }
        }
    }

    static class ApprovalStatusHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String path = exchange.getRequestURI().getPath();
            String requestId = path.substring(path.lastIndexOf("/") + 1);

            String status = approvalStatus.getOrDefault(requestId, "not_found");

            exchange.sendResponseHeaders(200, status.length());
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(status.getBytes());
            }
        }
    }

    static class PendingRequestsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            JsonObject response = new JsonObject();

            if (!pendingRequests.isEmpty()) {
                String requestId = pendingRequests.keys().nextElement();
                String image = pendingRequests.get(requestId);

                response.addProperty("requestId", requestId);
                response.addProperty("image", image);
                response.addProperty("hasPending", true);
            } else {
                response.addProperty("hasPending", false);
            }

            String jsonResponse = gson.toJson(response);

            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, jsonResponse.length());
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(jsonResponse.getBytes());
            }
        }
    }

    static class RespondHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            if ("POST".equals(exchange.getRequestMethod())) {
                InputStreamReader isr = new InputStreamReader(exchange.getRequestBody());
                BufferedReader br = new BufferedReader(isr);
                String requestBody = br.readLine();

                JsonObject json = gson.fromJson(requestBody, JsonObject.class);
                String requestId = json.get("requestId").getAsString();
                String response = json.get("response").getAsString();

                approvalStatus.put(requestId, response);
                pendingRequests.remove(requestId);

                String responseText = "Response recorded";
                exchange.sendResponseHeaders(200, responseText.length());
                try (OutputStream os = exchange.getResponseBody()) {
                    os.write(responseText.getBytes());
                }
            }
        }
    }

    private static String getIndexHtml() {
        return "<!DOCTYPE html><html><head><title>Face Recognition Approval</title><link rel='stylesheet' href='style.css'></head><body><div class='container'><h1>Face Recognition Approval</h1><div id='content'><p>Waiting for face detection requests...</p></div></div><script src='script.js'></script></body></html>";
    }

    private static String getStyleCss() {
        return "body{font-family:Arial,sans-serif;margin:0;padding:20px;background:#f0f0f0}.container{max-width:600px;margin:0 auto;background:white;padding:20px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}h1{text-align:center;color:#333}#faceImage{max-width:100%;height:auto;border:2px solid #ddd;border-radius:10px;margin:20px 0}.buttons{text-align:center;margin:20px 0}button{padding:15px 30px;margin:0 10px;font-size:16px;border:none;border-radius:5px;cursor:pointer}.approve{background:#4CAF50;color:white}.deny{background:#f44336;color:white}button:hover{opacity:0.8}";
    }

    private static String getScriptJs() {
        return "let currentRequestId=null;function checkForRequests(){fetch('/pending-requests').then(response=>response.json()).then(data=>{if(data.hasPending){showApprovalRequest(data.requestId,data.image)}else{showWaiting()}}).catch(err=>console.error(err))}function showApprovalRequest(requestId,imageData){currentRequestId=requestId;document.getElementById('content').innerHTML=`<h2>Approval Required</h2><img id='faceImage' src='data:image/jpeg;base64,${imageData}' alt='Detected Face'/><div class='buttons'><button class='approve' onclick='respond(\"approved\")'>APPROVE</button><button class='deny' onclick='respond(\"denied\")'>DENY</button></div>`}function showWaiting(){document.getElementById('content').innerHTML='<p>Waiting for face detection requests...</p>'}function respond(decision){if(!currentRequestId)return;fetch('/respond',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requestId:currentRequestId,response:decision})}).then(()=>{document.getElementById('content').innerHTML=`<h2>Response Sent: ${decision.toUpperCase()}</h2><p>Waiting for next request...</p>`;currentRequestId=null;setTimeout(checkForRequests,2000)}).catch(err=>console.error(err))}setInterval(checkForRequests,1000);checkForRequests();";
    }
}
