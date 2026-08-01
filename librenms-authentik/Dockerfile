FROM librenms/librenms:26.6.1

WORKDIR /opt/librenms

RUN lnms plugin:add socialiteproviders/authentik

# Kubernetes health endpoint
RUN printf '%s\n' \
    '<?php' \
    '' \
    '$output = [];' \
    '$exitCode = 1;' \
    '' \
    'exec("/usr/bin/lnms health:check 2>&1", $output, $exitCode);' \
    '' \
    'header("Content-Type: application/json");' \
    '' \
    'if ($exitCode === 0) {' \
    '    http_response_code(200);' \
    '    echo json_encode(["status" => "ok"]);' \
    '} else {' \
    '    http_response_code(503);' \
    '    echo json_encode(["status" => "unhealthy"]);' \
    '}' \
    > /opt/librenms/html/health.php
