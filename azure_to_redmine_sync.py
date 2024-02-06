import base64
import datetime
import os
import re
import signal
from unidecode import unidecode
import requests
from redminelib import Redmine
import math
import logging
from logging.handlers import RotatingFileHandler
import sys
from dotenv import load_dotenv
import argparse
import pdfkit
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# Configurar el analizador de argumentos
parser = argparse.ArgumentParser(description='Sincroniza tareas entre Azure y Redmine.')
parser.add_argument('sprint_number', type=int, help='Número del sprint que se desea sincronizar')

# Leer los argumentos de la línea de comandos
args = parser.parse_args()
sprint_number = args.sprint_number

load_dotenv()
wkhtmltopdf_path = os.getenv('WKHTMLTOPDF_PATH')
# Constants Azure
AZURE_DEVOPS_PROJECT_BASE = os.getenv('AZURE_DEVOPS_PROJECT_BASE')
AZURE_TEAM = os.getenv('AZURE_TEAM')
AZURE_DEVOPS_URL = f"{os.getenv('AZURE_DEVOPS_PROJECT_BASE')}{os.getenv('AZURE_TEAM')}_apis/wit/wiql?api-version=6.0"
AZURE_TOKEN = os.getenv('AZURE_TOKEN')
AREA_PATH = os.getenv('AREA_PATH')
ITERATION_PATH = os.getenv('ITERATION_PATH')

# Constants Redmine
REDMINE_URL = os.getenv('REDMINE_URL')
REDMINE_TOKEN = os.getenv('REDMINE_TOKEN')
PROJECT_ID = os.getenv('PROJECT_ID')
ID_CAMPO_HORAS_RESTANTES = os.getenv('ID_CAMPO_HORAS_RESTANTES')
ID_CAMPO_IBER_IDCLIENTE = os.getenv('ID_CAMPO_IBER_IDCLIENTE')

# Instancia del logging
logger = logging.getLogger('logger_sync_azure_redmine')
tiempo_inicio = datetime.datetime.now()
last_run_timestamp = None

#variables globales
version_sprint = None #Sprint
redmine = Redmine(REDMINE_URL, key=REDMINE_TOKEN)
issues_por_campo_personalizado = {}
mapeo_estados = {}
redmine_tipo_issue = {
    1 : 'Bug',
    2 : 'Evolutivo'
}
project_memberships = []
azure_redmine_user_map = {}

created_issues = []
failed_tasks = []
modified_tasks = [] 
none_modified_tasks = []

# region Configuracion Aplicacion

def cargar_ultimo_timestamp():
    global last_run_timestamp
    last_run_file = 'last_run.txt'

    if os.path.exists(last_run_file):
        with open(last_run_file, 'r') as f:
            last_run_timestamp = datetime.datetime.fromisoformat(f.read().strip())
    else:
        last_run_timestamp = datetime.datetime.now()

def actualizar_ultimo_timestamp():
    global last_run_timestamp
    last_run_file = 'last_run.txt'
    with open(last_run_file, 'w') as f:
        f.write(datetime.datetime.now().isoformat())
    last_run_timestamp = datetime.datetime.now()

def signal_handler(sig, frame):
    logger.info('Señal de cierre detectada. Cerrando la aplicación...')
    logger.info('--------------- Final del proceso de sincronizacion Azure <> Redmine ---------------')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def configurar_logging():    
    # Configuración de la rotación del log
    log_file_max_size = 30 * 1024 * 1024  # 30 MB
    backup_count = 5  # Número de archivos de backup

    logger.setLevel(logging.DEBUG)  # Capturar todos los niveles de log

    # Crea un handler para escribir mensajes de error (ERROR y CRITICAL) en error.log
    error_handler = RotatingFileHandler('error.log', maxBytes=log_file_max_size, backupCount=backup_count)
    error_handler.setLevel(logging.ERROR)  # Solo mensajes ERROR y CRITICAL
    error_formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(message)s')
    error_handler.setFormatter(error_formatter)
    logger.addHandler(error_handler)

    # Crea otro handler para escribir mensajes informativos (INFO) en synchronization.log
    info_handler = RotatingFileHandler('synchronization.log', maxBytes=log_file_max_size, backupCount=backup_count)
    info_handler.setLevel(logging.INFO)  # Solo mensajes INFO y DEBUG
    info_formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(message)s')
    info_handler.setFormatter(info_formatter)
    logger.addHandler(info_handler)

# endregion Configuracion Aplicacion

# region Funciones de soporte

def obtener_duracion_formateada():
    global tiempo_inicio
    tiempo_fin = datetime.datetime.now()
    duracion = tiempo_fin - tiempo_inicio        
    segundos = duracion.total_seconds()
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segundos = int(segundos % 60)
    
    return f"{horas}h {minutos}m {segundos}s"

def normalize_name(name):
    # Convertir a minúsculas y quitar acentos
    name = unidecode(name).lower()
    # Eliminar comas y normalizar espacios en blanco
    name = re.sub(r'\s+', ' ', name.replace(',', ' ')).strip()
    return name

# endregion Funciones de soporte

#region Obtencion de datos de Azure y Redmine de configuración

def cargar_miembros_proyecto():
    print("Obteniendo usuarios Redmine...")
    logger.info("Obteniendo usuarios Redmine...")
    global project_memberships
    project_memberships_raw = redmine.project_membership.filter(project_id=PROJECT_ID)
    
    project_memberships = []
    for miembro in project_memberships_raw:
        if hasattr(miembro, 'user'):
            project_memberships.append(miembro.user)
    procesado = f"Proceso realizado en {obtener_duracion_formateada()}."
    print(procesado)
    logger.info(procesado)

def obtener_mapear_estados_redmine():
    print("Obteniendo y mapeando estados Redmine <> Azure...")
    logger.info("Obteniendo y mapeando estados Redmine <> Azure...")
    url = f"{REDMINE_URL}/issue_statuses.json"
    headers = {'X-Redmine-API-Key': REDMINE_TOKEN}
    global mapeo_estados

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        estados = response.json().get('issue_statuses', [])
        estados_redmine  = {estado['name']: estado['id'] for estado in estados}    
        
        mapeo_estados = {
            'New': estados_redmine.get('Nueva', None),
            'Active': estados_redmine.get('En curso', None),
            'Waiting': estados_redmine.get('Pendiente', None),
            'Testing': estados_redmine.get('Pendiente cliente', None),
            'Closed': estados_redmine.get('Cerrada', None),
            'Resolved': estados_redmine.get('Resuelta', None),
            'Removed': estados_redmine.get('Desestimada', None),
        }
        procesado = f"Proceso realizado en {obtener_duracion_formateada()}."
        print(procesado)
        logger.info(procesado)
    except requests.RequestException as e:
        error_message = f"Error al obtener los estados de Redmine: {e}"
        print(error_message)
        logger.error(error_message, exc_info=True)
        sys.exit(1)

def buscar_version_segun_sprint(nombre_version):
    print("Obteniendo Id de la version del Sprint de Redmine...")
    logger.info("Obteniendo Id de la version del Sprint de Redmine...")
    global version_sprint
    proyecto = redmine.project.get(PROJECT_ID)   
    
    for version in proyecto.versions:
        if nombre_version.lower() in version.name.lower():
            version_sprint = version
            procesado = f"Proceso realizado en {obtener_duracion_formateada()}."
            print(procesado)
            logger.info(procesado)
            return   
    
    version_sprint = None    
    procesado = f"Proceso realizado en {obtener_duracion_formateada()}."
    print(procesado)
    logger.info(procesado)

# endregion Obtencion de datos de Azure y Redmine de configuración

#region Obtención de Trabajos de Azure y Redmine

def cargar_issues_Redmine():
    global issues_por_campo_personalizado
    print("Obteniendo tareas de Redmine...")
    logger.info("Obteniendo tareas de Redmine...")
    url = f"{REDMINE_URL}/projects/{PROJECT_ID}/issues.json"
    headers = {'X-Redmine-API-Key': REDMINE_TOKEN}
    issues = []
    offset = 0
    limit = 100  # Redmine suele tener un límite de 100 issues por página

    while True:
        params = {'offset': offset, 'limit': limit}
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            issues.extend(data['issues'])
            if len(data['issues']) < limit:
                break
            offset += limit
        else:
            error_msg = f"Error en la solicitud: {response.status_code}"
            print(error_msg)
            logger.error(error_msg)
            sys.exit(1)            

    issues_por_campo_personalizado = agrupar_issues_por_campo_personalizado(issues)
    procesado = f"Proceso realizado en {obtener_duracion_formateada()}."
    print(procesado)
    logger.info(procesado)    

def agrupar_issues_por_campo_personalizado(issues):
    issues_por_campo_personalizado = {}
    for issue in issues:
        key = obtener_clave_issue(issue)
        if key is not None:
            issues_por_campo_personalizado.setdefault(key, []).append(issue)
    return issues_por_campo_personalizado

def obtener_clave_issue(issue):
    """
    Obtiene un identificador único para una issue de Redmine, basado en el campo personalizado 'Iber_IdCliente'.
    
    Args:
        issue (dict): Una issue de Redmine representada como un diccionario.
        
    Returns:
        int: El identificador único de la issue, o None si no se puede determinar.
    """
    # Intentar obtener el ID del campo personalizado 'Iber_IdCliente'
    for custom_field in issue['custom_fields']:
        if custom_field['name'] == 'Iber_IdCliente' and custom_field['value'].strip() != '':
            try:
                return int(custom_field['value'])
            except ValueError:
                # Si el valor no puede ser convertido a int, retornar None
                return None 

    # Si no se encuentra el ID en los campos personalizados, intentar obtenerlo del campo 'subject'
    subject_parts = issue['subject'].split(' - ', 1)
    if len(subject_parts) > 1:
        try:
            return int(subject_parts[0])
        except ValueError:
            # Si el valor no puede ser convertido a int, retornar None
            return None
    
    # Si no se encuentra un identificador válido en ningún campo, retornar None
    return None

def get_azure_devops_tasks():
    """
    Obtiene las tareas de Azure DevOps para un área y una iteración específicas que han sido modificadas desde el último timestamp.  
        
    Returns:
        list: Una lista de tareas de Azure DevOps.
    """
    print("Obteniendo tareas de Azure DevOps...")
    logger.info("Obteniendo tareas de Azure DevOps...")

    # Ajustar el timestamp para asegurarse de no perder actualizaciones que ocurrieron durante el día de la última sincronización
    adjusted_timestamp = last_run_timestamp - datetime.timedelta(days=1)
    wiql_query = {
        "query": f"""
            Select 
                [System.Id], 
                [System.Title], 
                [System.Description], 
                [System.State], 
                [System.WorkItemType], 
                [System.AssignedTo], 
                [System.Parent], 
                [Microsoft.VSTS.Scheduling.OriginalEstimate], 
                [Microsoft.VSTS.Scheduling.RemainingWork]
            From WorkItems 
            Where 
                [System.AreaPath] = '{AREA_PATH}' 
                And [System.IterationPath] = '{ITERATION_PATH} {sprint_number}'
                And (
                    [System.WorkItemType] = 'Bug' 
                    Or [System.WorkItemType] = 'Task'
                    Or [System.WorkItemType] = 'User Story'
                )   
                And [System.ChangedDate] > '{adjusted_timestamp.strftime("%Y-%m-%d")}'             
        """
    }
    token = ':{}'.format(AZURE_TOKEN) 
    encoded_token = base64.b64encode(token.encode()).decode()
    headers = {'Authorization': f'Basic {encoded_token}', 'Content-Type': 'application/json'}
    response = requests.post(AZURE_DEVOPS_URL, json=wiql_query, headers=headers)
    if response.status_code == 200:
        work_item_ids = response.json()["workItems"]
        tasks = []
        for work_item in work_item_ids:
            task_url = '{}_apis/wit/workitems/{}?api-version=6.0&$expand=relations'.format(AZURE_DEVOPS_PROJECT_BASE, work_item['id'])            
            task_response = requests.get(task_url, headers=headers)
            if task_response.status_code == 200:
                task_data = task_response.json()
                work_item_type = task_data['fields'].get('System.WorkItemType')
                parent_id = None
                if work_item_type in ['Task', 'Bug']:                    
                    parent_id = task_data['fields'].get('System.Parent')                            
                task_data['parent_id'] = parent_id                 
                tasks.append(task_data)
            else:
                print(f"Error al obtener las tarea de Azure DevOps: {task_response.status_code}") 
                logger.error(f"Error al obtener las tarea de Azure DevOps: {task_response.status_code}")       
        return organize_work_items(tasks)
    else:
        print(f"Error al obtener las tareas de Azure DevOps: {response.status_code}")
        logger.error(f"Error al obtener las tareas de Azure DevOps: {response.status_code}")
        return None

def organize_work_items(work_items):
    organized = {}
    for item in work_items:
        item_id = item['id']
        item_type = item['fields']['System.WorkItemType']
        parent_id = item['fields'].get('System.Parent')
        task_info = {
            'id': item_id,
            'type': item_type,
            'data': item, 
            'children': []
        }

        if item_type in ['User Story', 'Feature']:
            organized[item_id] = task_info
        elif parent_id and parent_id in organized:
            organized[parent_id]['children'].append(task_info)    
    procesado = f"Proceso realizado en {obtener_duracion_formateada()}."
    print(procesado)
    logger.info(procesado)
    return organized

#endregion Obtención de Trabajos de Azure y Redmine

#region Procesamiento de tareas de Azure y Redmine

def process_work_items(work_items):
            total_tasks = sum(1 + len(US_info['children']) for US_info in work_items.values())
            total_parent_tasks = len(work_items) 
            print(f"Se van a Procesar un total de {total_parent_tasks} HUs con un total de {total_tasks} subtareas...")
            logger.info(f"Se van a Procesar un total de {total_parent_tasks} HUs con un total de {total_tasks} subtareas...")

            for id, US_info in work_items.items():
                parent_issue_id = process_task((id, US_info))
                if parent_issue_id:
                    for child_info in US_info['children']:
                        # Asegúrate de que child_info es un par clave-valor
                        child_id = child_info['id']
                        child_info['parent_id'] = parent_issue_id  # Establecer la ID de la tarea padre
                        process_task((child_id, child_info)) 

def process_task(taskazure):
            task_azure_id, task_info = taskazure  
            url_task_azure = task_info['data']['_links']['html']['href']
            texto_tarea_a_registrar = f"{task_azure_id} - '{task_info['data']['fields']['System.Title']}'"
            print(f"--- Procesando Tarea {texto_tarea_a_registrar} - Url: {url_task_azure} ---")      
            logger.info(f"--- Procesando Tarea {texto_tarea_a_registrar} - Url: {url_task_azure} ---")
            
            assigned_to_display_name = task_info['data']['fields'].get('System.AssignedTo', {}).get('displayName', '')
            assigned_to_unique_name = task_info['data']['fields'].get('System.AssignedTo', {}).get('uniqueName', '')
            assigned_to_id = buscar_usuario_redmine(assigned_to_display_name, assigned_to_unique_name, texto_tarea_a_registrar)
            
            new_redmine_task = {
                'id': task_azure_id,
                'type': task_info['data']['fields']['System.WorkItemType'],
                'state': task_info['data']['fields']['System.State'],
                'title': task_info['data']['fields']['System.Title'],
                'description': task_info['data']['fields'].get('System.Description', ''),
                'parentid': task_info.get('parent_id', None),
                'estimatedhours': task_info['data']['fields'].get('Microsoft.VSTS.Scheduling.OriginalEstimate', None),
                'remaininghours': task_info['data']['fields'].get('Microsoft.VSTS.Scheduling.RemainingWork', None),
                'assigned_to_id': assigned_to_id
            }

            print(f"Buscando en Redmine si existe la tarea {texto_tarea_a_registrar}...")
            logger.info(f"Buscando en Redmine si existe la tarea {texto_tarea_a_registrar}...")
            
            redmine_task = buscar_issue_por_campo_personalizado(task_azure_id)
            if redmine_task is None:
                return crear_nueva_tarea_redmine(new_redmine_task, texto_tarea_a_registrar)
            else:
                return actualizar_tarea_existente_redmine(redmine_task, new_redmine_task, texto_tarea_a_registrar)

#endregion Procesamiento de tareas de Azure y Redmine
            
#region Consulta de tareas de Redmine

def buscar_issue_por_campo_personalizado(taskid):
    try:
        logger.info(f"Buscando issue en Redmine por el campo personalizado 'Iber_IdCliente' con valor {taskid}...")
        # Buscar en el diccionario global por la clave 'taskid'
        issues_encontradas = issues_por_campo_personalizado.get(taskid)

        if issues_encontradas:
            # Si hay issues, retorna el ID de la primera encontrada
            return issues_encontradas[0]
        else:
            # No se encontraron issues con ese campo personalizado
            return None
    except Exception as e:
        error_message = f"Se ha producido un error al buscar la issue por el campo personalizado: {e}"
        print(error_message)
        logger.error(error_message, exc_info=True)
        sys.exit(1)

def buscar_usuario_redmine(nombre_usuario_azure, user_azure_id, texto_tarea_a_registrar):
    global azure_redmine_user_map
    print(f"Buscando usuario asignado {nombre_usuario_azure} de la tarea {texto_tarea_a_registrar} de Azure en Redmine ...")
    logger.info(f"Buscando usuario asignado {nombre_usuario_azure} de la tarea {texto_tarea_a_registrar} de Azure en Redmine ...")

    if nombre_usuario_azure == '':
        # Si el nombre del usuario de Azure está vacío, retorna None o un valor que represente "sin asignar"
        return None

    # Verificar si ya se ha buscado este usuario
    if user_azure_id in azure_redmine_user_map:
        return azure_redmine_user_map[user_azure_id]

    nombre_usuario_azure_normalizado = normalize_name(nombre_usuario_azure)
    partes_nombre_azure = set(nombre_usuario_azure_normalizado.split())
    for miembro in project_memberships:
        nombre_miembro_normalizado = normalize_name(miembro.name)
        # Dividir el nombre del miembro y convertirlo a un conjunto para facilitar la comparación
        partes_nombre_miembro = set(nombre_miembro_normalizado.split())

        # Comprobar si todas las partes del nombre del miembro están en el nombre de Azure
        if partes_nombre_miembro.issubset(partes_nombre_azure):
            azure_redmine_user_map[user_azure_id] = miembro.id
            return miembro.id

    # Si no se encuentra una coincidencia, asignar y retornar el ID de usuario por defecto de Redmine
    azure_redmine_user_map[user_azure_id] = 2666
    return 2666  # ID Usuario Redmine por defecto eaymerich

#endregion Consulta de tareas de Redmine

#region Tratamiento de Tareas Redmine

def crear_nueva_tarea_redmine(new_redmine_task, texto_tarea_a_registrar):
            global created_issues, failed_tasks
            print(f"Tarea {texto_tarea_a_registrar} no encontrada en Redmine. Creando...")
            logger.info(f"Tarea {texto_tarea_a_registrar} no encontrada en Redmine. Creando...")
            
            # Lógica para crear una nueva tarea en Redmine
            success, id_redmine = create_redmine_task(new_redmine_task)
            if success:
                print(f"Tarea {texto_tarea_a_registrar} creada en Redmine con ID: {id_redmine}")
                logger.info(f"Tarea '{new_redmine_task['title']}' creada en Redmine con ID: {id_redmine}")
                created_issues.append(f"{new_redmine_task['type']}: {id_redmine} - {new_redmine_task['title']}")
                print(f"--- Final del Procesado de la Tarea {texto_tarea_a_registrar} con Redmine Id {id_redmine} ---")
                logger.info(f"--- Final del Procesado de la Tarea {texto_tarea_a_registrar} con Redmine Id {id_redmine} ---")
                return id_redmine
            else:
                error_msg = f"No se ha podido crear la tarea de Redmine con ID {texto_tarea_a_registrar}. Error: {id_redmine}"
                print(error_msg)
                logger.error(error_msg)
                print(f"--- Final del Procesado de la Tarea {texto_tarea_a_registrar} con errores. Error {id_redmine} ---")
                logger.info(f"--- Final del Procesado de la Tarea {texto_tarea_a_registrar} con errores. Error {id_redmine} ---")
                failed_tasks.append(f"{new_redmine_task['type']}: {texto_tarea_a_registrar} Error: {id_redmine}")
                return None   

def create_redmine_task(task):
    headers = {'X-Redmine-API-Key': REDMINE_TOKEN}

    max_description_length = 65000
    description = task['description']
    if len(description) > max_description_length:
        description = description[:max_description_length]  
    task_data = {
        'issue': {            
            'project_id': PROJECT_ID,
            'tracker_id': redmine_tipo_issue.get(task['type'], 2), # Si task['type'] no está en redmine_tipo_issue, se usa un valor por defecto 2 (Evolutivo)            
            'fixed_version_id': version_sprint.id,            
            'subject': f"{task['id']} - {task['title']}",
            'description': description,            
            "custom_fields":
            [
                {"id": 13, "name": "Iber_Tarea_ADN", "value": "Proyecto migración plataforma"},
                {"id": 34, "name": "Versión solicitada", "value": version_sprint.name},
                {"id": 100,"name": "Iber_IdCliente", "value": str(task['id'])}
            ]  
        }
    }   

    estado_redmine = mapeo_estados.get(task['state'])
    if estado_redmine:
        task_data['issue']['status_id'] = estado_redmine 
    
    if task['assigned_to_id']:
        task_data['issue']['assigned_to_id'] = task['assigned_to_id']

    if task['parentid'] != None:        
        task_data['issue']['parent_issue_id'] = task['parentid'] 
        if task['estimatedhours'] is not None:
            task_data['issue']['estimated_hours'] = task['estimatedhours']
        if task['remaininghours'] is not None:         
            task_data['issue']['custom_fields'].append({"id": 36, "name": "Horas restantes", "value": str(int(math.ceil(task['remaininghours'])))})
             
    response = requests.post(REDMINE_URL+'issues.json', json=task_data, headers=headers)
    if response.status_code == 201:
        issue_id = response.json().get('issue', {}).get('id')
        return (True, issue_id)  # Retorna True i l'ID de la issue creada
    else:
        error_message = f"Error {response.status_code}: {response.text}"
        return (False, error_message)  # Retorna False i el missatge d'error

def actualizar_tarea_existente_redmine(redmine_task_found,new_redmine_task, texto_tarea_a_registrar):
    global modified_tasks, none_modified_tasks      
    redmine_task_id = redmine_task_found['id']
    typeTask = 'Tarea'

    redmine_task = redmine.issue.get(redmine_task_id)
    cambios_necesarios = necesita_actualizacion(new_redmine_task, redmine_task)
    if new_redmine_task['parentid'] is None and not cambios_necesarios:
        typeTask = 'HU'
    texto_tarea_encontrada = f"{typeTask}  {texto_tarea_a_registrar} encontrada en Redmine con Id {redmine_task_id}. Procesando..."
    print(texto_tarea_encontrada)
    logger.info(texto_tarea_encontrada)       
    
    if cambios_necesarios:
        if actualizar_tarea_redmine(redmine_task, cambios_necesarios):
            cambios_realizados = ", ".join([
                f"{campo}: {next((nombre_estado for nombre_estado, id_estado in mapeo_estados.items() if id_estado == valor), valor)}" if campo == 'estado'
                else f"{campo}: {next((user.name for user in project_memberships if user.id == valor), valor)}" if campo == 'assigned_to_id'
                else f"{campo}: {valor}"
                for campo, valor in cambios_necesarios.items()
            ])
            mensaje_modificacion = f"{new_redmine_task['type']}: {redmine_task_id} - {new_redmine_task['title']} (Cambios: {cambios_realizados})"

            #cambios_realizados = ", ".join([f"{campo}: {valor}" for campo, valor in cambios_necesarios.items()])
            #mensaje_modificacion = f"{new_redmine_task['type']}: {redmine_task_id} - {new_redmine_task['title']} (Cambios: {cambios_realizados})"

            texto_cambios_realizados = f"{typeTask} {texto_tarea_a_registrar} actualizada en Redmine con Id {redmine_task_id}. Cambios: {cambios_realizados}"
            print(texto_cambios_realizados)
            logger.info(texto_cambios_realizados)
            modified_tasks.append(mensaje_modificacion)
        else:
            texto_error_realizar_cambios = f"No se ha podido actualizar la {typeTask} de Redmine con ID {redmine_task_id}."
            print(texto_error_realizar_cambios)
            logger.error(texto_error_realizar_cambios)
            none_modified_tasks.append(f"{new_redmine_task['type']}: {redmine_task_id} - {new_redmine_task['title']}")
    else:
        texto_cambios_no_realizados = f"No se requieren actualizaciones para la {typeTask} {texto_tarea_a_registrar} con Redmine Id {redmine_task_id}."
        print(texto_cambios_no_realizados)
        logger.info(texto_cambios_no_realizados)
        none_modified_tasks.append(f"{new_redmine_task['type']}: {redmine_task_id} - {new_redmine_task['title']}")

    print(f"--- Final del Procesado de la {typeTask} {texto_tarea_a_registrar} con Redmine Id {redmine_task_id} ---")
    logger.info(f"--- Final del Procesado de la {typeTask} {texto_tarea_a_registrar} con Redmine Id {redmine_task_id} ---")
    return redmine_task_id

def calcular_porcentaje_realizado(horas_restantes, horas_totales_estimadas):
    if horas_totales_estimadas <= 0:
        return 100  # Evitar división por cero
    horas_realizadas = horas_totales_estimadas - horas_restantes
    porcentaje_realizado = (horas_realizadas / horas_totales_estimadas) * 100
    return max(0, min(100, round(porcentaje_realizado)))

def necesita_actualizacion(task_azure, redmine_task):
    cambios = {}    
    
    if task_azure['parentid'] is None:
        #Campos a actualizar cuando es una HU padre
        campos_a_actualizar = ['estado', 'azure_id', 'version_sprint_id']
    else:
        
        campos_a_actualizar = ['estado', 'azure_id', 'version_sprint_id','horas_restantes', 'porcentaje_realizado', 'assigned_to_id']

    # Actualizaciones comunes
    if 'estado' in campos_a_actualizar:
        estado_redmine = mapeo_estados.get(task_azure['state'])
        if estado_redmine and redmine_task.status.id != estado_redmine:
            cambios['estado'] = estado_redmine

    if 'azure_id' in campos_a_actualizar:
        if str(task_azure['id']) != redmine_task.custom_fields.get(int(ID_CAMPO_IBER_IDCLIENTE)).value:
            cambios['azure_id'] = str(task_azure['id'])

    if 'version_sprint_id' in campos_a_actualizar:
        fixed_version_actual = getattr(redmine_task.fixed_version, 'id', None) if hasattr(redmine_task, 'fixed_version') else None
        if fixed_version_actual != version_sprint.id:
            cambios['version_sprint_id'] = version_sprint.id

    # Actualizaciones condicionales
    if 'horas_restantes' in campos_a_actualizar and task_azure['remaininghours'] is not None:
        horas_restantes_redmine = str(int(math.ceil(task_azure['remaininghours'])))
        campo_horas_restantes = redmine_task.custom_fields.get(int(ID_CAMPO_HORAS_RESTANTES))
        if campo_horas_restantes is not None and campo_horas_restantes.value != horas_restantes_redmine:
            cambios['horas_restantes'] = horas_restantes_redmine

    if 'porcentaje_realizado' in campos_a_actualizar and task_azure['remaininghours'] is not None:
        horas_totales_estimadas = getattr(redmine_task, 'estimated_hours', 0)
        porcentaje_realizado = calcular_porcentaje_realizado(int(horas_restantes_redmine), horas_totales_estimadas)
        if porcentaje_realizado != redmine_task.done_ratio:
            cambios['porcentaje_realizado'] = porcentaje_realizado

    if 'assigned_to_id' in campos_a_actualizar:
        assigned_to_id_redmine = getattr(redmine_task.assigned_to, 'id', None) if hasattr(redmine_task, 'assigned_to') else None
        if task_azure['assigned_to_id'] and assigned_to_id_redmine != task_azure['assigned_to_id']:
            cambios['assigned_to_id'] = task_azure['assigned_to_id']

    return cambios

def actualizar_tarea_redmine(redmine_task, cambios):
    try:
        tarea = redmine.issue.get(redmine_task['id'])
        actualizacion_realizada = False

        if 'estado' in cambios:
            tarea.status_id = cambios['estado']
            actualizacion_realizada = True

        if 'horas_restantes' in cambios:
            tarea.custom_fields = [{'id': ID_CAMPO_HORAS_RESTANTES, 'value': cambios['horas_restantes']}]
            actualizacion_realizada = True

        if 'porcentaje_realizado' in cambios:
            tarea.done_ratio = cambios['porcentaje_realizado']
            actualizacion_realizada = True        

        if 'assigned_to_id' in cambios:
            if cambios['assigned_to_id'] is None:
                tarea.assigned_to_id = '' 
            else:
                tarea.assigned_to_id = cambios['assigned_to_id']
            actualizacion_realizada = True

        if 'azure_id' in cambios:
            tarea.custom_fields = [{'id': ID_CAMPO_IBER_IDCLIENTE, 'value': cambios['azure_id']}]
            actualizacion_realizada = True

        if 'version_sprint_id' in cambios:
            tarea.fixed_version_id = version_sprint.id
            tarea.custom_fields = [{'id': 34, 'value': version_sprint.name}]
            actualizacion_realizada = True

        if actualizacion_realizada:
            tarea.save()
            print(f"La tarea {tarea.id} - {tarea.subject} se ha actualizado correctamente en Redmine.")
            logger.info(f"La tarea {tarea.id} - {tarea.subject} se ha actualizado correctamente en Redmine.")
            return True
        else:
            print(f"La tarea {tarea.id} - {tarea.subject} ya está actualizada en Redmine.")
            logger.info(f"La tarea {tarea.id} - {tarea.subject} ya está actualizada en Redmine.")
            return False
    except Exception as e:
        error_message = f"Error al actualizar la tarea en Redmine {tarea.id} - {tarea.subject}. Error: {e}"
        print(error_message)
        logger.error(error_message, exc_info=True)
        return False

def anadir_entrada_tiempo(api_key, id_issue, horas, actividad_id, fecha, comentarios=''):
    """
    Añade una nueva entrada de tiempo a una issue en Redmine usando la API key del usuario.

    :param api_key: API key del usuario que añade la entrada de tiempo.
    :param id_issue: ID de la issue a la que se le añadirá el tiempo.
    :param horas: Número de horas trabajadas.
    :param actividad_id: ID de la actividad (enumeración en Redmine).
    :param fecha: Fecha en la que se gastaron las horas (formato 'YYYY-MM-DD').
    :param comentarios: Comentarios opcionales para la entrada de tiempo.
    """
    try:
        # Crear una instancia de Redmine usando la API key del usuario
        redmine_imputacion = Redmine(REDMINE_URL, key=api_key)

        # Crear la entrada de tiempo
        time_entry = redmine_imputacion.time_entry.create(
            issue_id=id_issue,
            hours=horas,
            activity_id=actividad_id,
            spent_on=fecha,
            comments=comentarios
        )
        return time_entry
    except Exception as e:
        print(f"Error al añadir la entrada de tiempo: {e}")
        return None
    
#endregion Tratamiento de Tareas Redmine
    
def main():  
    exito = True  
    try:       
        cargar_ultimo_timestamp()
        configurar_logging()
        print("Iniciando proceso de sincronizacion Azure <> Redmine...") 
        logger.info("--------------- Iniciando proceso de sincronizacion Azure <> Redmine ---------------")                               
        cargar_miembros_proyecto()             
        obtener_mapear_estados_redmine()                
        buscar_version_segun_sprint("Sprint 104")                
        cargar_issues_Redmine()                                 
        azure_tasks = get_azure_devops_tasks()                   

        if azure_tasks:
            process_work_items(azure_tasks)   
            escribir_resultados_ejecucion(created_issues, failed_tasks, modified_tasks, none_modified_tasks)   

        actualizar_ultimo_timestamp()
                
    except Exception as e:
            escribir_resultados_ejecucion(created_issues, failed_tasks, modified_tasks, none_modified_tasks)
            total_tasks = sum(1 + len(US_info['children']) for US_info in azure_tasks.values())
            total_parent_tasks = len(azure_tasks) 
            exito = False            

            error_message = f"\nError durante la ejecución del programa: {e}"
            print(error_message)
            logger.error(error_message, exc_info=True)
            generar_resumen_html(total_parent_tasks, total_tasks, created_issues, modified_tasks, failed_tasks, none_modified_tasks, exito, error_message)
            print(f"Proceso de sincronización completado en {obtener_duracion_formateada()}. Generando el archivo de resultados...")
            logger.info('--------------- Final con errores del proceso de sincronizacion Azure <> Redmine ---------------')
            sys.exit(1)

    print(f"Proceso de sincronización completado en {obtener_duracion_formateada()}. Generando el archivo de resultados...")
    logger.info(f"Proceso de sincronización completado en {obtener_duracion_formateada()}. Generando el archivo de resultados...")
    logger.info('--------------- Final del proceso de sincronizacion Azure <> Redmine ---------------')
    
    total_tasks = sum(1 + len(US_info['children']) for US_info in azure_tasks.values())
    total_parent_tasks = len(azure_tasks) 
    generar_resumen_html(total_parent_tasks, total_tasks, created_issues, modified_tasks, failed_tasks, none_modified_tasks, exito)

def escribir_resultados_ejecucion(created_issues, failed_tasks, modified_tasks, none_modified_tasks):    

    logger.info("Escribiendo resultados en el logger...")
    logger.info(f"Sincronización completada en en {obtener_duracion_formateada()}\n")
    logger.info("Resultado de la sincronización\n")    
    
    logger.info("Issues creadas:")
    logger.info("-----------------")
    for issue in created_issues:
        logger.info(f"- {issue}")

    logger.info("IDs de tareas fallidas:")
    logger.info("------------------------")
    for task_info in failed_tasks:
        logger.info(f"- {task_info}")

    logger.info("Tareas modificadas:")
    logger.info("-------------------")
    for taskinfo in modified_tasks:
        logger.info(f"- {taskinfo}")

    logger.info("Tareas No modificadas:")
    logger.info("-------------------")
    for taskinfo in none_modified_tasks:
        logger.info(f"- {taskinfo}")

def generar_resumen_html(total_parent_tasks, total_tasks, created_issues, modified_tasks, failed_tasks, none_modified_tasks, exito, mensaje_error = ''):
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="X-UA-Compatible" content="IE=edge">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Resumen de Ejecución</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 20px;
                padding: 20px;
                background-color: #f4f4f4;
            }}
            h1 {{
                color: #333;
            }}
            .summary {{
                background-color: #fff;
                padding: 20px;
                margin-bottom: 20px;
                border-radius: 5px;
                box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
            }}
            .task-list {{
                list-style-type: none;
                padding: 0;
            }}
            .task-list li {{
                padding: 5px;
                border-bottom: 1px solid #ccc;
            }}
            .task-list li:last-child {{
                border-bottom: none;
            }}
        </style>
    </head>
    <body>
        <h1>Resumen de Sincronización Azure <> Redmine</h1>
        <div class="summary">
            <h2>Detalles de Ejecución:</h2>
            <p>Inicio: {tiempo_inicio}</p>
            <p>Duración: {obtener_duracion_formateada()}</p>
            <p>Versión del Sprint: {version_sprint.name}</p>           
        </div>
        <div class="summary">
            <h2>Estadísticas:</h2>
            <p>Total de HUs procesadas: {total_parent_tasks}</p>
            <p>Total de subtareas procesadas: {total_tasks}</p>
            <p>Total de Issues Creadas: {len(created_issues)}</p> 
            <p>Total de Issues Modificadas: {len(modified_tasks)}</p>
            <p>Total de Issues No Modificadas: {len(none_modified_tasks)}</p>
            <p>Total de Issues Fallidas: {len(failed_tasks)}</p>
        </div>
        <div class="summary">
            <h2>Tareas Creadas:</h2>
            <ul class="task-list">
                {''.join(f"<li>{issue}</li>" for issue in created_issues)}
            </ul>
        </div>
        <div class="summary">
            <h2>Tareas Modificadas:</h2>
            <ul class="task-list">
                {''.join(f"<li>{task}</li>" for task in modified_tasks)}
            </ul>
        </div>
        <div class="summary">
            <h2>Tareas No Modificadas:</h2>
            <ul class="task-list">
                {''.join(f"<li>{task}</li>" for task in none_modified_tasks)}
            </ul>
        </div>
        <div class="summary">
            <h2>Tareas Fallidas:</h2>
            <ul class="task-list">
                {''.join(f"<li>{task}</li>" for task in failed_tasks)}
            </ul>
        </div>
    </body>
    </html>
    """

    with open('resumen_ejecucion.html', 'w', encoding='utf-8') as file:
        file.write(html_content)
    
    # Configuración para asegurarse de que encuentra wkhtmltopdf
    config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
    # Convertir HTML a PDF
    nombre_archivo_pdf = 'resumen_ejecucion.pdf'
    pdfkit.from_string(html_content, nombre_archivo_pdf, configuration=config)   

    # Datos del correo
    destinatarios = ['eaymerich@hiberus.com']
    asunto = 'Resumen de Ejecución de la Sincronización'
    if exito:
        cuerpo = f'Se ha procesado correctamente el proceso de sincronización en {obtener_duracion_formateada()}.\n\nSe adjunta el resumen.\n\nSaludos'
        asunto += ' - Ejecutado correctamente'
    else:
        cuerpo = f'Se ha encontrado un error durante la ejecución del proceso de sincronización.\n\n{mensaje_error}\n\nSe adjunta el resumen.\n\nSaludos'
        asunto += ' - Error'

    # Llamar a la función enviar_correo para enviar el PDF generado
    enviar_correo(destinatarios, asunto, cuerpo, nombre_archivo_pdf) 

def enviar_correo(destinatarios, asunto, cuerpo, archivo_pdf):
    # Configuración del servidor SMTP y credenciales de acceso
    servidor_smtp = os.getenv('SMTP_SERVER')
    puerto_smtp = int(os.getenv('SMTP_PORT'))
    usuario_smtp = os.getenv('SMTP_USER')
    contraseña_smtp = os.getenv('SMTP_PASSWORD')

    # Crear el mensaje
    mensaje = MIMEMultipart()
    mensaje['From'] = usuario_smtp
    mensaje['To'] = ", ".join(destinatarios)
    mensaje['Subject'] = asunto
    mensaje.attach(MIMEText(cuerpo, 'plain'))

    # Adjuntar el archivo PDF
    try:
        with open(archivo_pdf, 'rb') as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(archivo_pdf))
        # Después de leer el archivo, añade los headers necesarios
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(archivo_pdf)}"'
        mensaje.attach(part)
    except Exception as e:
        print(f'Ocurrió un error al adjuntar el archivo PDF: {e}')
        return

    # Enviar el correo
    with smtplib.SMTP(servidor_smtp, puerto_smtp) as servidor:
        servidor.starttls()
        servidor.login(usuario_smtp, contraseña_smtp)
        servidor.send_message(mensaje)

    print('Correo de resultados enviado correctamente.')

if __name__ == "__main__":
    main()
