import axios from 'axios';

interface HealthCheckResult {
  service: string;
  url: string;
  status: 'OK' | 'ERROR';
  httpStatus?: number;
  errorCode?: string;
  errorMessage?: string;
  responseData?: any;
  latencyMs: number;
}

async function checkApi(name: string, url: string, options = {}): Promise<HealthCheckResult> {
  const start = Date.now();
  try {
    const response = await axios.get(url, { timeout: 5000, ...options });
    return {
      service: name,
      url,
      status: 'OK',
      httpStatus: response.status,
      latencyMs: Date.now() - start
    };
  } catch (error: any) {
    const latencyMs = Date.now() - start;
    if (error.response) {
      // La API respondió, pero con un status de error (4xx / 5xx)
      return {
        service: name,
        url,
        status: 'ERROR',
        httpStatus: error.response.status,
        errorCode: 'HTTP_ERROR_RESPONSE',
        errorMessage: `Respuesta de error de la API. Status: ${error.response.status}`,
        responseData: error.response.data,
        latencyMs
      };
    } else if (error.request) {
      // La petición fue enviada pero no hubo respuesta (caída de conexión / timeout / DNS)
      return {
        service: name,
        url,
        status: 'ERROR',
        errorCode: error.code || 'NO_RESPONSE',
        errorMessage: error.message,
        latencyMs
      };
    } else {
      // Error al configurar la petición
      return {
        service: name,
        url,
        status: 'ERROR',
        errorCode: 'REQUEST_SETUP_ERROR',
        errorMessage: error.message,
        latencyMs
      };
    }
  }
}

// Ejemplo de uso guardando reporte detallado en JSON
import * as fs from 'fs';

async function runAllChecks() {
  const apis = [
    { name: 'Auth Service', url: 'https://api.tudominio.com/auth/health' },
    { name: 'Payments API', url: 'https://api.tudominio.com/payments/v1/ping' },
  ];

  const results = await Promise.all(apis.map(a => checkApi(a.name, a.url)));
  
  // Guardamos el resultado en un archivo JSON para que Roo Code lo lea
  fs.writeFileSync('health-report.json', JSON.stringify(results, null, 2));
  console.log('Reporte generado en health-report.json');
}

runAllChecks();

