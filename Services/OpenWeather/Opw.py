from flask import Flask, request, abort
from flask_restful import Resource, Api, reqparse
from flask_sqlalchemy import SQLAlchemy
from flask_oauthlib.provider import OAuth2Provider
from functools import wraps
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from secrets import token_bytes
from configurationParser import Configuration
from datetime import datetime, timedelta
import logging
import coloredlogs
import os
import requests

logger = logging.getLogger('OpenWeather Logger')
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s', datefmt="%H:%M:%S")
ch.setFormatter(formatter)
logger.addHandler(ch)
coloredlogs.install(level='DEBUG', logger=logger, fmt='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - '
                                                      '%(message)s', datefmt="%H:%M:%S")

fileHandler = logging.FileHandler("{}.log".format('OpenWeather'))
fileHandler.setFormatter(formatter)
logger.addHandler(fileHandler)


app = Flask('OpenWeather')
app.logger.addHandler(ch)
app.secret_key = 'developement'
app.config.update({'SQLALCHEMY_DATABASE_URI': 'sqlite:///oauth_openweather.sqlite'})
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
# as provider
oauth = OAuth2Provider(app)
api = Api(app)
opw_key = os.environ['OPW_KEY']
JWT = None

error_message = lambda x, y, z: {'error': x, 'msg': y, 'code': z}

def require_authentication(f):
    """Do not provide authorization until gets the authentication."""
    def wrapper(*args, **kwargs):
        logger.info('Validating jwt')
        if request.method == 'POST':
            jwt_bearer = request.get_json()['jwt-bearer']
            logger.info(jwt_bearer)
        else:
            jwt_bearer = request.args['jwt-bearer']
            logger.info(jwt_bearer)
        if jwt_bearer:
            validate = requests.get(SERVICES['AUTHENTICATION']['VALIDATE'], params={'jwt': jwt_bearer}, headers={'Authorization':'Bearer ' + JWT}).json()
            if validate['ack'] == 'true':
                kwargs['service_name'] = validate['audience']
                return f(*args, **kwargs)
        return {'ack': 'false',
                'msg': 'Authentication Requited.'}, 403
    return wrapper


def require_oauth(*scopes):
    """Protect resource with specified scopes."""
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            logger.info('Check authorization')
            if request.method == 'GET':
                access_token = request.args['access_token']
                logger.info(access_token)
            else:
                access_token = request.get_json()['access_token']
                logger.info(access_token)
            if access_token is not None:
                scopes_ = list(scopes)
                token_object = Token.query.join(Client, Token.client_id == Client.client_id)\
                    .add_columns(Token._scopes, Token.access_token, Client.service_name)\
                    .filter(Token.access_token == access_token).first()

                if token_object:
                    logger.info('Valid token')
                    if all(x in token_object._scopes for x in scopes_):
                        logger.info('Valid authorization')
                        kwargs['app_name'] = token_object.service_name
                        return f(*args, **kwargs)
            return abort(403)
        return decorated
    return wrapper


# Service
class User(db.Model):
    # service_id
    id = db.Column(db.Integer, primary_key=True)
    service_name = db.Column(db.String(40), unique=True)


class Client(db.Model):
    client_id = db.Column(db.String(100), primary_key=True)
    client_secret = db.Column(db.String(200), nullable=False)
    # it will have just 1 value
    user_id = db.Column(db.ForeignKey('user.id'), default='1')
    user = db.relationship('User')

    _redirect_uris = db.Column(db.Text)
    _default_scopes = db.Column(db.Text)
    service_name = db.Column(db.String(200), unique=True)

    @property
    def client_type(self):
        return 'public'

    @property
    def redirect_uris(self):
        if self._redirect_uris:
            return self._redirect_uris.split()
        return []

    @property
    def default_redirect_uri(self):
        return self.redirect_uris[0]

    @property
    def default_scopes(self):
        if self._default_scopes:
            return self._default_scopes.split()
        return []


class Grant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # service_id
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'))
    # Service
    user = db.relationship('User')

    client_id = db.Column(db.String(100), db.ForeignKey('client.client_id'),
                          nullable=False, )
    client = db.relationship('Client')

    code = db.Column(db.String(255), index=True, nullable=False)

    redirect_uri = db.Column(db.String(255))
    expires = db.Column(db.DateTime)

    _scopes = db.Column(db.Text)

    def delete(self):
        db.session.delete(self)
        db.session.commit()
        return self

    @property
    def scopes(self):
        if self._scopes:
            return self._scopes.split()
        return []


class Token(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(100), db.ForeignKey('client.client_id'), nullable=False)
    client = db.relationship('Client')
    # service_id
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    # Service
    user = db.relationship('User')

    # currently only bearer is supported
    token_type = db.Column(db.String(40))

    access_token = db.Column(db.String(255), unique=True)
    refresh_token = db.Column(db.String(255), unique=True)
    expires = db.Column(db.DateTime)
    _scopes = db.Column(db.Text)

    @property
    def scopes(self):
        if self._scopes:
            return self._scopes.split()
        return []


@oauth.clientgetter
def load_client(client_id):
    return Client.query.filter_by(client_id=client_id).first()


@oauth.grantgetter
def load_grant(client_id, code):
    return Grant.query.filter_by(client_id=client_id, code=code).first()


@oauth.grantsetter
def save_grant(client_id, code, request, *args, **kwargs):
    # decide the expires time yourself
    expires = datetime.utcnow() + timedelta(seconds=100)
    client = Client.query.filter_by(client_id=client_id).first()
    # Service object(id, name)
    user = User.query.get(client.user_id)
    if not user:
        logger.warning('User not found')
        exit()
    grant = Grant(
        client_id=client_id,
        code=code['code'],
        redirect_uri=request.redirect_uri,
        _scopes=' '.join(request.scopes),
        user=user,
        expires=expires
    )
    db.session.add(grant)
    db.session.commit()
    return grant


@oauth.tokengetter
def load_token(access_token=None, refresh_token=None):
    if access_token:
        return Token.query.filter_by(access_token=access_token).first()
    elif refresh_token:
        return Token.query.filter_by(refresh_token=refresh_token).first()


@oauth.tokensetter
def save_token(token, request, *args, **kwargs):
    toks = Token.query.filter_by(
        client_id=request.client.client_id,
        user_id=request.user.id  # default
    )
    # make sure that every client has only one token connected to a user
    for t in toks:
        db.session.delete(t)

    expires_in = token.pop('expires_in')
    expires = datetime.utcnow() + timedelta(seconds=expires_in)

    tok = Token(
        access_token=token['access_token'],
        refresh_token=token['refresh_token'],
        token_type=token['token_type'],
        _scopes=token['scope'],
        expires=expires,
        client_id=request.client.client_id,
        user_id=request.user.id,  # default
    )
    db.session.add(tok)
    db.session.commit()
    return tok


# OAuth Provider
class Authorization(Resource):
    @staticmethod
    @require_authentication
    @oauth.authorize_handler
    def get(*args, **kwargs):
        logger.info('Authorize Handle')
        return True

    @staticmethod
    @oauth.token_handler
    def post():
        logger.info('Token Handle')
        return {'service': app.name}


class AuthorizationManagment(Resource):
    # create app
    @staticmethod
    @require_authentication
    def post(service_name):
        parser = reqparse.RequestParser()
        parser.add_argument('redirect_uri', type=str, location='json', required=True,
                            help='redirect_uri cannot be blank')
        parser.add_argument('scopes', type=str, location='json', required=True, help='scopes cannot be blank')
        parser.add_argument('jwt-bearer', type=str, location='json', required=True, help='JWT cannot be blank')
        args = parser.parse_args(strict=True)

        client = Client.query.filter_by(service_name=service_name).first()
        if client:
            return {'client_id': client.client_id,
                'client_secret': client.client_secret}, 200
                
        client_id = token_bytes(16)
        digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
        digest.update(client_id.hex().encode('utf-8'))
        client_secret = digest.finalize().hex()
        client = Client(client_id=client_id.hex(),
                        client_secret=client_secret,
                        _redirect_uris=args['redirect_uri'],
                        _default_scopes=args['scopes'],
                        user_id=1,
                        service_name=service_name)
        db.session.add(client)
        db.session.commit()
        return {'client_id': client_id.hex(),
                'client_secret': client_secret}, 200


class OpenW(Resource):
    @require_oauth('basic')
    def post(self, app_name):
        parser = reqparse.RequestParser()
        parser.add_argument('lat', type=str, location='json',required=True, help='Latitude cannot be blank')
        parser.add_argument('long', type=str, location='json', required=True, help='Longitude cannot be blank')
        parser.add_argument('day', type=str, location='json', required=True, help='Day cannot be blank')
        parser.add_argument('hour', type=str, location='json', required=True, help='Hour cannot be blank')
        data = parser.parse_args()
        print (data)

        lat = data['lat']
        long = data['long']
        json_day = data['day']
        json_hour = int(data['hour'])
        url = 'http://api.openweathermap.org/data/2.5/forecast?lat='+lat+'&lon='+long+'&cnt=50&APPID='+opw_key

        try:
            opw = requests.get(url)
        except requests.exceptions.RequestException as e:
            logger.exception(e)
            return error_message('OpenW', 'Internal error', 500)

        response = opw.json()['list']

        for i in response:
            timestamp = i['dt']
            date = datetime.fromtimestamp(timestamp)
            day = str(date.day)
            hour = int(date.hour)
            tem_K = i['main']['temp']
            tem_C = tem_K -273.15
            condition = i['weather'][0]['main']
            if json_day == day and ((json_hour-hour)>-3 and (json_hour-hour)<3):
                logger.info('HTTP POST Weather - successfully processed')
                logger.info('{}'.format(tem_C))
                return {'hour': hour, 'temp': "%.1f" % tem_C, 'condition': condition, 'day': day}
        return []


class Internal(Resource):
    @staticmethod
    def get():
        return authentication()

def authentication():
    global JWT
    req = requests.post(SERVICES['AUTHENTICATION']['POST'],
                        json={'username': SERVICES['AUTHENTICATION']['USERNAME']})
    nonce = req.json()['nonce']
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(nonce.encode('utf-8'))
    nonce_digest = digest.finalize().hex()

    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(SERVICES['AUTHENTICATION']['PASSWORD'].encode('utf-8'))
    password_digest = digest.finalize().hex()

    req = requests.get(SERVICES['AUTHENTICATION']['GET'], auth=(SERVICES['AUTHENTICATION']['USERNAME'], nonce_digest + password_digest))
    JWT = req.json()['jwt-bearer']
    # REMOVE THEN
    logger.info('{}'.format(JWT))
    return {'ack':'true'}


api.add_resource(OpenW, '/Opw/')
api.add_resource(Internal, '/Opw/v1.1/internal/')
api.add_resource(Authorization, '/Opw/v1.1/authorization/')
api.add_resource(AuthorizationManagment, '/Opw/v1.1/authorization_managment/')

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = 'true'
    db.create_all()
    config = Configuration(filename='conf.ini')
    SERVICES = config.service_config
    user = User.query.filter_by(service_name='OpenWeather').first()
    if not user:
        user = User(id=1, service_name='OpenWeather')
        db.session.add(user)
        db.session.commit()
    app.run(port=5009, host='0.0.0.0', threaded=True)