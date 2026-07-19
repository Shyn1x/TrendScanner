import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import { createProxyMiddleware } from "http-proxy-middleware";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

// Proxy everything else to Streamlit on port 5000
app.use(
  "/",
  createProxyMiddleware({
    target: "http://localhost:5000",
    changeOrigin: true,
    ws: true,
    on: {
      error: (_err, _req, res) => {
        if (res && "writeHead" in res) {
          (res as import("http").ServerResponse).writeHead(503);
          (res as import("http").ServerResponse).end(
            "Trendscanner is starting up — please refresh in a moment.",
          );
        }
      },
    },
  }),
);

export default app;
