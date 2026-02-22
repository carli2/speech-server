<?php
// Proxy for Piper multi-server with GET support and helper voice lists
// Internal upstream URL (do not pass from client)
$proxy = 'http://127.0.0.1:5000/';
if (substr($proxy, -1) !== '/') { $proxy .= '/'; }

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Methods: GET, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type, Accept');
    exit;
}

function only_localhost($url) {
    return (bool)preg_match("#^https?://(127\\.0\\.0\\.1|localhost)(:\\d+)?(/.*)?$#i", $url);
}

if ($_SERVER['REQUEST_METHOD'] === 'GET' && isset($_GET['action'])) {
    header('Access-Control-Allow-Origin: *');
    $action = $_GET['action'];
    if ($action === 'voices') {
        // Use internal proxy target
        $target = $proxy;
        $nocache = isset($_GET['nocache']) && $_GET['nocache'] === '1';
        if (!function_exists('curl_init')) { http_response_code(500); header('Content-Type: text/plain'); echo 'cURL extension not available'; exit; }
        $ch = curl_init();
        $url = $target . 'voices' . ($nocache ? ('?_=' . rawurlencode((string)time())) : '');
        curl_setopt($ch, CURLOPT_URL, $url);
        curl_setopt($ch, CURLOPT_HTTPGET, true);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_HTTPHEADER, [ 'Accept: application/json', 'Expect:' ]);
        $resp = curl_exec($ch);
        if ($resp === false) { http_response_code(502); header('Content-Type: text/plain'); echo 'Proxy error: ' . curl_error($ch); curl_close($ch); exit; }
        $code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE) ?: 200;
        curl_close($ch);
        http_response_code($code);
        header('Content-Type: application/json; charset=utf-8');
        if ($nocache) { header('Cache-Control: no-store, no-cache, must-revalidate'); header('Pragma: no-cache'); }
        echo $resp;
        exit;
    }
    if ($action === 'trained') {
        $root = getcwd();
        $base = $root . DIRECTORY_SEPARATOR . 'voices-piper';
        $list = [];
        if (is_dir($base)) {
            foreach (glob($base . DIRECTORY_SEPARATOR . '*.onnx') as $onnx) {
                $bn = basename($onnx);
                $id = preg_replace('/\.onnx$/i', '', $bn);
                $name = $id;
                $list[] = [ 'id' => $id, 'name' => $name, 'file' => $bn, 'path' => $onnx ];
            }
        }
        header('Content-Type: application/json; charset=utf-8');
        if (isset($_GET['nocache']) && $_GET['nocache'] === '1') { header('Cache-Control: no-store, no-cache, must-revalidate'); header('Pragma: no-cache'); }
        echo json_encode([ 'trained' => $list ], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
        exit;
    }
}

// Proxy GET to Piper multi-server and stream WAV
if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    header('Access-Control-Allow-Origin: *');
    header('X-Accel-Buffering: no');
    // Cache policy: allow long cache by default, override if nocache=1
    $nocache = isset($_GET['nocache']) && $_GET['nocache'] === '1';
    if ($nocache) {
        header('Cache-Control: no-store, no-cache, must-revalidate');
        header('Pragma: no-cache');
    } else {
        header('Cache-Control: public, max-age=31536000');
    }

    // Use internal proxy target
    $target = $proxy;

    // Build upstream URL with same query (except target)
    $qs = $_GET; unset($qs['target']);
    $query = http_build_query($qs);
    // Add cache-busting token upstream if nocache
    if ($nocache) {
        $query .= ($query ? '&' : '') . '_=' . rawurlencode((string)time());
    }
    $url = $target . ($query ? ('?' . $query) : '');

    if (!function_exists('curl_init')) { http_response_code(500); header('Content-Type: text/plain'); echo 'cURL extension not available'; exit; }
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_HTTPGET, true);
    curl_setopt($ch, CURLOPT_HEADER, false);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, false);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [ 'Accept: audio/wav', 'Expect:' ]);
    // Ensure the PHP process notices when the client disconnects
    @ignore_user_abort(false);
    // Abort upstream when client disconnects during streaming
    // Propagate upstream status and content-type via header callback
    curl_setopt($ch, CURLOPT_HEADERFUNCTION, function ($ch, $headerLine) {
        $line = trim($headerLine);
        if ($line === '') return strlen($headerLine);
        if (stripos($line, 'HTTP/') === 0) {
            // Example: HTTP/1.1 404 Not Found
            $parts = explode(' ', $line, 3);
            if (count($parts) >= 2) {
                $code = intval($parts[1]);
                if ($code >= 100 && $code <= 599) { @http_response_code($code); }
            }
        } elseif (stripos($line, 'Content-Type:') === 0) {
            $ct = trim(substr($line, strlen('Content-Type:')));
            if ($ct !== '') { @header('Content-Type: ' . $ct); }
        }
        return strlen($headerLine);
    });
    curl_setopt($ch, CURLOPT_WRITEFUNCTION, function ($ch, $data) {
        if (function_exists('connection_aborted') && connection_aborted()) {
            // Returning 0 tells cURL to abort the transfer
            return 0;
        }
        echo $data;
        @ob_flush(); flush();
        return strlen($data);
    });
    $ok = curl_exec($ch);
    if ($ok === false) { http_response_code(502); header('Content-Type: text/plain'); echo 'Proxy error: ' . curl_error($ch); }
    curl_close($ch);
    exit;
}

http_response_code(405);
header('Content-Type: text/plain');
echo 'Method not allowed';
